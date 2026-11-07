"""Motor: a fachada única para o app perguntar ao modelo (DOFL).

Todos os módulos (comando, notas, regras, calendário, floreios, secretário…) chamam `motor.run([SemReq…])`
exatamente como chamavam `engine.run`; o Motor fica na frente do SemanticEngine e cuida de:

* **lote automático** — pedidos concorrentes de vários módulos que chegam enquanto o modelo trabalha (ou dentro
  de uma janela de poucos ms) viram UMA chamada ao engine (as linhas são independentes: mesmas respostas);
* **cache LRU com TTL** dos resultados por (tipo, texto, opções…) — a mesma notificação lida pelo secretário,
  pelas automações e pelos alertas é perguntada ao modelo uma vez só; pedidos repetidos dentro do mesmo lote
  também são feitos uma vez só;
* **cache de tokenização** compartilhado: o texto de origem é tokenizado UMA vez para todas as perguntas
  sobre ele (CHOICE + NOUL + resolve do mesmo pedido).
  Os estados do encoder NÃO são reaproveitados entre perguntas: no DOFL a pergunta, as opções e o texto passam
  juntos pelo backbone + adaptador (atenção completa sobre a linha inteira — veja serialize.Serializer.rows e
  onnx_backend._encoder_graph), então o estado do texto depende da pergunta. Reaproveitar mudaria as respostas;
* **truncamento inteligente** só nas perguntas de classificação (CHOICE/NOUL/SCORE/SET/RANK): espaços repetidos
  colapsados e, em textos muito longos, começo + fim (onde ficam assunto, valores e assinatura), cortando em
  espaço — em vez de várias janelas deslizantes. `resolve`/`answer` nunca são cortados (as posições
  dos trechos precisam bater com o texto);
* **aquecimento em segundo plano** (a primeira pergunta de verdade não paga a inicialização do ONNX Runtime);
* **respostas que não dependem do lote**: em pacote int8 dinâmico cada linha vai sozinha ao encoder
  (veja politica_lote); o lote do Motor continua juntando os pedidos (uma fila, sem disputa de threads);
* **descarregar ocioso** integrado: quando Models descarrega o modelo, o Motor solta o engine e o recarrega
  sozinho (via `carregar`) no próximo pedido que não estiver no cache;
* **métricas**: ms por pedido, acertos do cache, lotes, memória do processo.
"""
from __future__ import annotations

import copy
import logging
import re
import threading
import time
from collections import OrderedDict
from dataclasses import replace
from typing import Callable, List, Optional

log = logging.getLogger("djorge.motor")

CLASSIFICA = ("CHOICE", "NOUL", "SCORE", "SET", "RANK")
PADRAO = {"ativo": True, "janela_ms": 3.0, "cache_max": 1024, "ttl_s": 900.0, "tok_cache_max": 4096,
          "max_chars": 2400, "lote_max": 96, "aquecer": True}


class _LRU:
    """LRU com TTL e trava (vários módulos chamam ao mesmo tempo)."""

    def __init__(self, maximo: int, ttl_s: float = 0.0):
        self.maximo, self.ttl = int(maximo), float(ttl_s or 0)
        self._d: "OrderedDict" = OrderedDict()
        self._lock = threading.Lock()
        self.acertos = self.erros = 0

    def get(self, k):
        with self._lock:
            v = self._d.get(k)
            if v is None:
                self.erros += 1
                return None
            t, x = v
            if self.ttl and time.monotonic() - t > self.ttl:
                del self._d[k]
                self.erros += 1
                return None
            self._d.move_to_end(k)
            self.acertos += 1
            return x

    def put(self, k, x):
        if self.maximo <= 0:
            return
        with self._lock:
            self._d[k] = (time.monotonic(), x)
            self._d.move_to_end(k)
            while len(self._d) > self.maximo:
                self._d.popitem(last=False)

    def clear(self):
        with self._lock:
            self._d.clear()

    def __len__(self):
        return len(self._d)

    def info(self) -> dict:
        n = self.acertos + self.erros
        return {"acertos": self.acertos, "erros": self.erros, "tamanho": len(self._d), "maximo": self.maximo,
                "taxa": round(self.acertos / n, 4) if n else 0.0}


class _Pedido:
    __slots__ = ("reqs", "adapter", "evento", "resultado", "erro")

    def __init__(self, reqs, adapter):
        self.reqs, self.adapter = reqs, adapter
        self.evento = threading.Event()
        self.resultado = self.erro = None


def chave(r) -> tuple:
    return (r.kind, r.source, r.mode or "", r.target or "", r.definition or "", tuple(r.options or ()),
            tuple(r.descriptions or ()), r.ask or "", round(float(r.threshold or 0), 6))


_ESPACOS = re.compile(r"[ \t ]{2,}")
_LINHAS = re.compile(r"\n\s*\n(\s*\n)+")


def encurtar(texto: str, max_chars: int) -> str:
    """Texto de classificação: espaços repetidos colapsados; se ainda for longo demais, 70 % do começo + 30 % do
    fim, cortados em espaço, com ' … ' no meio."""
    t = _LINHAS.sub("\n\n", _ESPACOS.sub(" ", str(texto or ""))).strip()
    if not max_chars or len(t) <= max_chars:
        return t
    ini, fim = int(max_chars * 0.7), int(max_chars * 0.3) - 3
    a = t[:ini]
    if " " in a[-80:]:
        a = a[:a.rfind(" ")]
    b = t[-fim:] if fim > 0 else ""
    if " " in b[:80]:
        b = b[b.find(" ") + 1:]
    return a.rstrip() + " … " + b.lstrip()


class Motor:
    """Fachada do modelo. `engine`: SemanticEngine (ou qualquer objeto com .run(reqs)). `carregar()`: devolve o
    engine quando ele foi descarregado (Models.get). `ao_usar()`: avisado a cada pedido (ociosidade)."""

    def __init__(self, engine=None, carregar: Optional[Callable] = None, ao_usar: Optional[Callable] = None,
                 **conf):
        self.conf = dict(PADRAO, **{k: v for k, v in conf.items() if v is not None})
        self._eng = None
        self._ident = None
        self.lote_invariante = None
        self.carregar, self.ao_usar = carregar, ao_usar
        self.cache = _LRU(self.conf["cache_max"], self.conf["ttl_s"])
        self.tok = _LRU(self.conf["tok_cache_max"])
        self._fila: List[_Pedido] = []
        self._lock = threading.Lock()
        self._ocupado = False
        self._eng_lock = threading.Lock()
        self.m = {"pedidos": 0, "perguntas": 0, "ao_modelo": 0, "lotes": 0, "juntados": 0, "repetidos": 0,
                  "encurtados": 0, "ms_total": 0.0, "ms_modelo": 0.0, "ms_ultimo": None, "aquecido_ms": None,
                  "carregamentos": 0}
        if engine is not None:
            self.trocar(engine)

    # ------------------------------------------------------------- compatibilidade com o SemanticEngine --
    @property
    def engine(self):
        """`getattr(z.semantic, "engine", z.semantic)` (comando, regras…) continua caindo no Motor."""
        return self

    def __getattr__(self, nome):          # meta, stats, model_id, tok, ser, model… -> engine de verdade
        if nome.startswith("__") or nome in ("_eng", "conf", "m"):
            raise AttributeError(nome)
        eng = self.__dict__.get("_eng") or self._garantir()
        return getattr(eng, nome)

    # ------------------------------------------------------------------------------- engine (troca) --
    def trocar(self, engine):
        """Novo engine (carregou / recarregou depois de ocioso / otimizou): instala o cache de tokenização.
        Resultados em cache só valem para o mesmo modelo (model_id + arquivo ONNX)."""
        alvo = getattr(engine, "engine", engine) if not isinstance(engine, Motor) else engine._eng
        ident = (getattr(alvo, "model_id", None), str((getattr(alvo, "meta", {}) or {}).get("onnx")))
        if self._ident not in (None, ident):
            self.cache.clear()
        self._ident = ident
        self._instalar_tok(alvo)
        self.lote_invariante = politica_lote(alvo, self.conf.get("lote_linhas"))
        self._eng = engine
        return self

    def soltar(self):
        """Models descarregou o modelo: solta a referência (a memória volta ao sistema). O cache fica."""
        self._eng = None
        self.tok.clear()

    def carregado(self) -> bool:
        return self._eng is not None

    def _garantir(self):
        eng = self._eng
        if eng is not None:
            return eng
        with self._eng_lock:
            if self._eng is None:
                if self.carregar is None:
                    raise RuntimeError("modelo não carregado")
                self.m["carregamentos"] += 1
                novo = self.carregar()
                if self._eng is None and novo is not None:     # carregar() normalmente já chamou trocar()
                    self.trocar(novo)
            return self._eng

    def _instalar_tok(self, eng):
        """Memoriza Serializer._enc (texto -> ids/offsets) no engine: a tokenização do texto do pedido é feita
        uma vez para todas as perguntas sobre ele. As listas devolvidas só são lidas/fatiadas pelo Serializer."""
        ser = getattr(eng, "ser", None)
        if ser is None or getattr(ser, "_motor_tok", None) is self.tok:
            return
        orig = getattr(ser, "_enc_original", None) or getattr(ser, "_enc", None)
        if orig is None:
            return
        cache = self.tok

        def _enc(text, offsets=False):
            k = (text, bool(offsets))
            v = cache.get(k)
            if v is None:
                v = orig(text, offsets)
                v = (list(v[0]), [tuple(x) for x in v[1]] if v[1] is not None else None)
                cache.put(k, v)
            return v
        ser._enc_original = orig
        ser._enc = _enc
        ser._motor_tok = cache

    # ----------------------------------------------------------------------------------- perguntar --
    def preparar(self, r):
        """Truncamento inteligente (só classificação)."""
        if r.kind in CLASSIFICA and isinstance(r.source, str):
            t = encurtar(r.source, int(self.conf.get("max_chars") or 0))
            if t != r.source:
                self.m["encurtados"] += 1
                return replace(r, source=t)
        return r

    def run(self, reqs, tenant_adapter: Optional[str] = None, timings: Optional[dict] = None):
        reqs = list(reqs or [])
        if not reqs:
            return []
        t0 = time.perf_counter()
        if self.ao_usar is not None:
            try:
                self.ao_usar()
            except Exception:
                pass
        if not self.conf.get("ativo", True) or tenant_adapter:
            eng = self._garantir()
            out = eng.run(reqs, tenant_adapter=tenant_adapter, timings=timings)
            self._contar(len(reqs), len(reqs), t0)
            return out
        prep = [self.preparar(r) for r in reqs]
        keys = [chave(r) for r in prep]
        res, falta = [None] * len(prep), {}
        for i, k in enumerate(keys):
            v = self.cache.get(k)
            if v is not None:
                res[i] = v
            elif k in falta:
                falta[k].append(i)
                self.m["repetidos"] += 1
            else:
                falta[k] = [i]
        if falta:
            uniq = [prep[ids[0]] for ids in falta.values()]
            out = self._pelo_lote(uniq)
            for (k, ids), v in zip(falta.items(), out):
                self.cache.put(k, v)
                for i in ids:
                    res[i] = v
        if timings is not None:
            timings["motor"] = {"cache": len(prep) - sum(len(v) for v in falta.values()), "modelo": len(falta)}
        self._contar(len(reqs), len(falta), t0)
        return [copy.deepcopy(v) for v in res]           # quem chama pode mexer no resultado sem sujar o cache

    def perguntar(self, kind: str, texto: str, **kw):
        """Atalho: motor.perguntar("CHOICE", texto, options=[...], ask="...") -> resultado."""
        from djorge.runtime.semantic import SemReq
        return self.run([SemReq(kind, texto, **kw)])[0]

    def _contar(self, n, ao_modelo, t0):
        dt = (time.perf_counter() - t0) * 1e3
        self.m["pedidos"] += 1
        self.m["perguntas"] += n
        self.m["ao_modelo"] += ao_modelo
        self.m["ms_total"] += dt
        self.m["ms_ultimo"] = round(dt, 2)

    # --------------------------------------------------------------------------------- lote automático --
    def _pelo_lote(self, reqs):
        """Líder/seguidor: quem chega com o modelo livre vira líder, espera a janela (poucos ms) e leva junto
        tudo o que estiver na fila; quem chega com o modelo ocupado espera o próximo lote do líder."""
        p = _Pedido(reqs, None)
        with self._lock:
            self._fila.append(p)
            lider = not self._ocupado
            if lider:
                self._ocupado = True
        if not lider:
            if not p.evento.wait(timeout=120):
                raise TimeoutError("o modelo não respondeu a tempo")
        else:
            janela = float(self.conf.get("janela_ms") or 0) / 1000.0
            if janela > 0:
                time.sleep(janela)
            while True:
                with self._lock:
                    grupo, n = [], 0
                    while self._fila and (not grupo or n + len(self._fila[0].reqs) <= self.conf["lote_max"]):
                        q = self._fila.pop(0)
                        grupo.append(q)
                        n += len(q.reqs)
                    if not grupo:
                        self._ocupado = False
                        break
                self._executar(grupo)
        if p.erro is not None:
            raise p.erro
        return p.resultado

    def _executar(self, grupo):
        todas = [r for q in grupo for r in q.reqs]
        t = time.perf_counter()
        try:
            out = self._garantir().run(todas)
            i = 0
            for q in grupo:
                q.resultado = out[i:i + len(q.reqs)]
                i += len(q.reqs)
        except Exception as e:                           # todos recebem o erro; ninguém fica esperando
            for q in grupo:
                q.erro = e
        self.m["ms_modelo"] += (time.perf_counter() - t) * 1e3
        self.m["lotes"] += 1
        self.m["juntados"] += len(grupo)
        for q in grupo:
            q.evento.set()

    # ------------------------------------------------------------------------------------ aquecimento --
    def aquecer(self, fundo: bool = True):
        """Uma pergunta curta fora do cache: inicializa as sessões/kernels do ONNX Runtime em segundo plano."""
        def go():
            try:
                from djorge.runtime.semantic import SemReq
                t = time.perf_counter()
                eng = self._eng
                if eng is None:
                    return
                with self._lock:
                    if self._ocupado:
                        return                     # já há pedido de verdade rodando: ele mesmo aquece
                eng.run([SemReq("NOUL", "Aquecendo o modelo.", options=("o texto fala de dinheiro",)),
                         SemReq("CHOICE", "Aquecendo o modelo.", options=("sim", "não"), ask="Ok?")])
                self.m["aquecido_ms"] = round((time.perf_counter() - t) * 1e3, 1)
            except Exception as e:
                log.debug("aquecer: %s", e)
        if not self.conf.get("aquecer", True):
            return None
        if not fundo:
            go()
            return None
        th = threading.Thread(target=go, daemon=True, name="aquecer-modelo")
        th.start()
        return th

    # ------------------------------------------------------------------------------------------ info --
    def limpar(self):
        self.cache.clear()
        self.tok.clear()

    def metricas(self) -> dict:
        m = self.m
        try:
            from .leveza import memoria_mb
            mem = memoria_mb()
        except Exception:
            mem = None
        eng = self._eng
        alvo = getattr(eng, "engine", eng) if eng is not None and not isinstance(eng, Motor) else None
        st = dict(getattr(alvo, "stats", {}) or {}) if alvo is not None else {}
        return {"carregado": eng is not None, "pedidos": m["pedidos"], "perguntas": m["perguntas"],
                "ao_modelo": m["ao_modelo"],
                "ms_medio": round(m["ms_total"] / m["pedidos"], 2) if m["pedidos"] else None,
                "ms_por_pergunta_modelo": round(m["ms_modelo"] / m["ao_modelo"], 2) if m["ao_modelo"] else None,
                "ms_ultimo": m["ms_ultimo"], "lotes": m["lotes"],
                "media_por_lote": round(m["juntados"] / m["lotes"], 2) if m["lotes"] else None,
                "repetidos_no_lote": m["repetidos"], "encurtados": m["encurtados"],
                "cache": self.cache.info(), "cache_tokenizacao": self.tok.info(),
                "aquecido_ms": m["aquecido_ms"], "recarregamentos": m["carregamentos"],
                "forward_calls": st.get("forward_calls"), "memoria_mb": mem,
                "janela_ms": self.conf.get("janela_ms"), "threads": _threads_info(alvo),
                "lote_invariante": getattr(self, "lote_invariante", None),
                "linhas_por_chamada": getattr(alvo, "batch_size", None) if alvo is not None else None}


DINAMICOS = ("all", "all_fr", "mlp", "attn", "embed")


def politica_lote(eng, forcar=None) -> bool:
    """O int8 DINÂMICO (quantize_dynamic) calcula a escala das ativações sobre o tensor do lote inteiro, com o
    preenchimento: a resposta de um texto muda conforme os vizinhos do lote (medido no pacote candidato: até
    0,66 de probabilidade num NOUL). Nesses pacotes cada linha vai sozinha ao encoder (batch_size=1) — mais
    lento (~1,3x), mas a resposta é sempre a mesma. Pacotes fp32/fp16/int8-só-pesos (wo8) ou marcados
    `lote_invariante: true` no onnx/encoder.json continuam em lote. `forcar`: True/False manual (motor.lote_linhas)."""
    import json
    import os
    if forcar is not None:
        inv = bool(forcar)
    else:
        inv = True
        meta = getattr(eng, "meta", {}) or {}
        ox = meta.get("onnx")
        if ox and getattr(eng, "artifact_path", None):
            try:
                info = json.load(open(os.path.join(eng.artifact_path, "onnx", "encoder.json"), encoding="utf-8"))
            except Exception:
                info = {}
            quant = str(info.get("quant") or "all")
            if "int8" in str(ox.get("file", "")):
                inv = bool(info["lote_invariante"]) if "lote_invariante" in info else not (
                    quant in DINAMICOS or quant.startswith("sens"))
    if not inv and hasattr(eng, "batch_size"):
        eng.batch_size = 1
    return inv


def _threads_info(eng):
    try:
        enc = eng.model.encode
        so = enc.sess.get_session_options()
        return {"intra": so.intra_op_num_threads, "inter": so.inter_op_num_threads,
                "arena": so.enable_cpu_mem_arena}
    except Exception:
        return None
