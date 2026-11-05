"""Triagem de mensagens RECEBIDAS (e-mail, WhatsApp, notificações): o que é, quão importante, o que fazer e onde pôr.

Pensada para quem recebe muito: cada mensagem sai com
  categoria   pessoal | trabalho | financeiro | compras e entregas | agenda | promoções | notificações | segurança
  prioridade  urgente | alta | normal | baixa  (+ nota 0-100 e os motivos)
  ações       responder | agendar | pagar | criar tarefa | acompanhar entrega | usar código | ler depois | arquivar
  destino     onde inserir no app (card de agenda, tarefa com prazo, financeiro, entregas, arquivo)
  dados       datas, horas, valores, boleto, rastreio, código, link de reunião, entidades (com o nome tirado do e-mail)
Regras leves dão sinais com peso; com o DOFL (engine) a categoria e a segurança vêm do modelo e as regras ajustam.

    from djorge.runtime.triagem import triar
    triar("Oi! A fatura de R$ 230,00 vence amanhã ...", remetente="cobranca@enel.com.br", engine=eng)
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Dict, Iterable, List, Optional

from .recognizers import _sem_acento, nome_do_email, reconhecer

CATEGORIAS = {
    "pessoal": "conversa pessoal de amigos ou família",
    "trabalho": "assunto de trabalho, cliente, projeto ou estudo",
    "financeiro": "cobrança, fatura, boleto, pagamento, banco ou pix",
    "compras e entregas": "pedido de compra, envio, entrega ou rastreio",
    "agenda": "convite, reunião, consulta ou compromisso com data",
    "promoções": "propaganda, oferta, cupom ou newsletter",
    "notificações": "aviso automático de sistema, login, código ou conta",
}
SINAIS = {   # categoria: [(regex sem acento, peso)]
    "financeiro": [(r"\b(boleto|fatura|vencimento|vence|pagamento|pagar|cobranca|debito|pix|transferencia|"
                    r"extrato|parcela|mensalidade|nota fiscal)\b", 2.0)],
    "compras e entregas": [(r"\b(pedido|entrega|enviado|despachado|rastreio|rastreamento|transportadora|"
                            r"saiu para entrega|a caminho|chegou|devolucao)\b", 2.0)],
    "agenda": [(r"\b(reuniao|convite|call|agenda|consulta|compromisso|encontro|marcad[ao]|remarcar|horario)\b", 1.5)],
    "promoções": [(r"\b(oferta|desconto|cupom|promocao|imperdivel|so hoje|frete gratis|black friday|liquidacao|"
                   r"descadastr|unsubscribe|newsletter|% off)\b", 2.0)],
    "notificações": [(r"\b(codigo de verificacao|seu codigo|login|novo acesso|senha|verifique|confirmar e-mail|"
                      r"notificacao|nao responda|noreply|no-reply)\b", 2.0)],
    "trabalho": [(r"\b(projeto|relatorio|cliente|prazo|entregavel|aprovacao|proposta|contrato|planilha|apresentacao|"
                  r"equipe|gestor|sprint|demanda|orcamento)\b", 1.5)],
    "pessoal": [(r"\b(saudade|beijo|bjs|abraco|familia|mae|pai|amor|aniversario|churrasco|fim de semana)\b", 1.5)],
}
URGENTE = r"\b(urgente|urgencia|imediatamente|o quanto antes|asap|hoje ainda|ultimo dia|prazo final|importante|atencao)\b"
PERGUNTA = r"(\?|\b(pode|poderia|consegue|conseguiria|me confirma|confirma|aguardo (seu )?retorno|me avisa|me responde|" \
           r"o que acha|topa|vamos)\b)"
PEDIDO = r"\b(preciso que|precisamos que|favor|por favor|pode enviar|envie|mande|revisar|revise|assinar|assine|" \
         r"preencher|preencha|aprovar|aprove|responder ate)\b"


NIVEIS = ["baixa", "normal", "alta", "urgente"]
SEGURANCA = {"seguro": "legítima, sem ameaça", "spam": "propaganda em massa não solicitada",
             "golpe": "engana a pessoa para pagar ou transferir dinheiro",
             "phishing": "rouba senhas, códigos ou dados de cartão se passando por alguém",
             "malware": "entrega programa ou arquivo malicioso"}
ASK_PEDIDO = "Esta mensagem pede que quem a recebe faça alguma coisa."      # NOUL (sim/não)
ASK_FRASE = "Qual destas frases é o pedido feito a quem recebe?"            # CHOICE entre as frases
# verbo de pedido: imperativo -> infinitivo (o nome da tarefa sai no infinitivo)
IMPERATIVO = {"envie": "enviar", "envia": "enviar", "mande": "mandar", "manda": "mandar", "revise": "revisar",
              "revisa": "revisar", "assine": "assinar", "assina": "assinar", "preencha": "preencher",
              "preenche": "preencher", "aprove": "aprovar", "aprova": "aprovar", "responda": "responder",
              "responde": "responder", "ligue": "ligar", "liga": "ligar", "confirme": "confirmar",
              "confirma": "confirmar", "faca": "fazer", "faz": "fazer", "traga": "trazer", "traz": "trazer",
              "compre": "comprar", "compra": "comprar", "pague": "pagar", "paga": "pagar", "verifique": "verificar",
              "verifica": "verificar", "veja": "ver", "ve": "ver", "olhe": "olhar", "olha": "olhar",
              "passe": "passar", "passa": "passar", "agende": "agendar", "agenda": "agendar", "marque": "marcar",
              "marca": "marcar", "leia": "ler", "le": "ler", "busque": "buscar", "busca": "buscar",
              "separe": "separar", "separa": "separar", "atualize": "atualizar", "atualiza": "atualizar",
              "corrija": "corrigir", "corrige": "corrigir", "imprima": "imprimir", "imprime": "imprimir",
              "lembre": "lembrar", "avise": "avisar", "avisa": "avisar", "retorne": "retornar"}
_INF = r"[a-z]+(?:ar|er|ir|or)"
PEDIDO_FRASE = re.compile(
    r"\b(?:(?:voce |vc )?(?:pode|poderia|podia|consegue|conseguiria|daria para|da pra|tem como)\s+(?:me\s+)?(?P<a>" + _INF +
    r")|(?:preciso|precisamos|gostaria|queria)\s+(?:que (?:voce |vc )?(?P<b>[a-z]+)|de\s+(?:voce\s+)?(?P<c>" + _INF + r"))"
    r"|(?:por favor|favor|pfv|pf)\s*,?\s*(?:de\s+)?(?:me\s+)?(?P<d>[a-z]+)|nao (?:esquece|esqueca) de\s+(?P<e>" + _INF + r")"
    r"|(?:^|[,;:]\s*|\s)(?:me\s+)?(?P<f>" + "|".join(sorted(IMPERATIVO, key=len, reverse=True)) + r")\b)")
_ABERTURA = re.compile(r"(?i)^\s*(?:(?:oi|ol[aá]|bom dia|boa tarde|boa noite|e a[ií]|opa|prezad[oa]s?|caro|cara)\b[^,.!?\n]{0,30}[,.!]\s*)+")
_CAUDA = re.compile(r"(?i)[\s,;]*(?:por favor|pfv|pf|pra mim|para mim|obrigad[oa]|valeu|ok)?[\s?!.,;]*$")


def _frases(texto: str) -> List[str]:
    return [f.strip() for f in re.split(r"(?<=[.!?])\s+|\n+", texto or "") if len(f.strip()) >= 3]


def _modelo(engine, corpo: str, texto: str) -> Optional[Dict]:
    """Todas as perguntas da triagem num só engine.run (lote): categoria, segurança, urgência, é pedido?, quem pediu
    e qual frase é o pedido. Falha do modelo -> None (valem as regras)."""
    from .semantic import SemReq
    src = corpo[:1500]
    frases = _frases(texto)[:8]
    reqs = [SemReq("CHOICE", src, options=list(CATEGORIAS), descriptions=list(CATEGORIAS.values()),
                   ask="Que tipo de mensagem é esta?"),
            SemReq("CHOICE", src, options=list(SEGURANCA), descriptions=list(SEGURANCA.values()),
                   ask="Esta mensagem é segura ou uma ameaça?"),
            SemReq("SCORE", src, options=NIVEIS, ask="Qual a urgência desta mensagem para quem a recebe?"),
            SemReq("NOUL", src, options=[ASK_PEDIDO]),
            SemReq("resolve", src, mode="ENTITY", target="pessoa", threshold=0.5)]
    if len(frases) >= 2:
        reqs.append(SemReq("CHOICE", src, options=frases, ask=ASK_FRASE))
    try:
        out = engine.run(reqs)
    except Exception:
        return None
    m = {"categoria": out[0], "seguranca": out[1], "urgencia": out[2], "pedido": out[3], "src": src}
    ms = [x for x in (getattr(out[4], "matches", None) or []) if float(getattr(x, "probability", 0)) >= 0.5]
    m["pessoa"] = src[ms[0].start:ms[0].end].strip() if ms else None
    if len(out) > 5:
        m["frase"] = out[5]
    return m


def _sem_datas(frase: str) -> str:
    """Tira da frase os trechos de data/hora ('até sexta', 'amanhã às 9h'): eles vão para o prazo, não para o nome."""
    try:
        ts = [t for t in reconhecer(frase) if t.campo in ("data", "hora", "período do dia")]
    except Exception:
        ts = []
    for t in sorted(ts, key=lambda t: -t.ini):
        ini = t.ini
        m = re.search(r"(?i)\b(?:at[eé]|para|pra|no|na|em|dia|[aà]s?|de)\s+$", frase[:ini])
        if m:
            ini = m.start()
        frase = frase[:ini] + frase[t.fim:]
    return re.sub(r"\s{2,}", " ", frase).strip()


def _tarefa(frase: str) -> str:
    """'Oi Ana, você pode revisar a proposta até sexta?' -> 'Revisar a proposta'."""
    f = _ABERTURA.sub("", frase).strip()
    f = _sem_datas(f)
    s = _sem_acento(f.casefold())
    m = PEDIDO_FRASE.search(s)
    if m:
        g = next(k for k in "abcdef" if m.group(k))
        verbo = m.group(g)
        resto = f[m.end(g):]
        verbo = IMPERATIVO.get(verbo, verbo)
        f = verbo + resto
    f = re.sub(r"(?i)^(?:me\s+|nos\s+)", "", f)
    f = _CAUDA.sub("", f).strip(" ,;:-")
    f = re.sub(r"(?i)\s+(?:por favor|pfv)\b", "", f)
    return (f[:1].upper() + f[1:])[:90] if f else ""


def _titulo(tarefa: str, n: int = 6) -> str:
    ws = tarefa.split()
    t = " ".join(ws[:n])
    t = re.sub(r"(?i)\s+(?:a|o|as|os|de|do|da|dos|das|e|para|pra|com|no|na|em|um|uma)$", "", t)
    return t.rstrip(" ,;:.") + ("…" if len(ws) > n else "")


def extrair_pedido(texto: str, assunto: Optional[str] = None, remetente: Optional[str] = None,
                   prazo: Optional[str] = None, valores=(), trechos=None, mod: Optional[Dict] = None) -> Optional[Dict]:
    """A mensagem pede algo? -> {titulo, tarefa, texto_central, quem, prazo, valor, p, fonte} | None.
    Com o modelo: NOUL decide se é pedido, CHOICE escolhe a frase central e resolve acha quem pediu; as regras
    (frase com verbo de pedido) são pista. Sem o modelo, só as regras."""
    frases = _frases(texto) or ([assunto] if assunto else [])
    regra = [f for f in frases if PEDIDO_FRASE.search(_sem_acento(f.casefold()))]
    central, fonte, p = None, "regras", None
    if mod is not None:
        p = float(mod["pedido"].get("p_true", 0.0))
        if p < 0.5 and not (regra and p >= 0.35):
            return None
        fonte = "modelo"
        fr = mod.get("frase")
        if fr and fr.get("answer") in frases and float(fr.get("probability", 0)) >= 0.4:
            central = fr["answer"]
        else:
            central = regra[0] if regra else (frases[0] if frases else None)
    elif regra:
        central = regra[0]
    if not central:
        return None
    tarefa = _tarefa(central) or _ABERTURA.sub("", central).strip()
    quem = (mod or {}).get("pessoa") or remetente or None
    tc = _ABERTURA.sub("", central).strip()
    return {"titulo": _titulo(tarefa), "tarefa": tarefa, "texto_central": tc[:1].upper() + tc[1:],
            "quem": quem, "prazo": prazo, "valor": (list(valores) or [None])[0],
            "p": round(p, 3) if p is not None else None, "fonte": fonte,
            "quem_do_texto": bool((mod or {}).get("pessoa"))}


def triar(texto: str, remetente: Optional[str] = None, assunto: Optional[str] = None, engine=None,
          vips: Iterable[str] = (), hoje: Optional[_dt.date] = None) -> Dict:
    hoje = hoje or _dt.date.today()
    corpo = f"{assunto or ''}\n{texto}".strip()
    s = _sem_acento(corpo.casefold())
    dados = reconhecer(corpo, hoje)
    por = {}
    for d in dados:
        por.setdefault(d.campo, []).append(d)
    motivos: List[str] = []

    # ---- categoria: regras (+ modelo, se houver) ----
    nota = {c: 0.0 for c in CATEGORIAS}
    for c, regras in SINAIS.items():
        for rx, w in regras:
            nota[c] += w * len(re.findall(rx, s))
    if "boleto" in por or "valor" in por:
        nota["financeiro"] += 1.5
    if "rastreio" in por:
        nota["compras e entregas"] += 3
    if "link de reunião" in por:
        nota["agenda"] += 3
    elif "data" in por and "hora" in por:
        nota["agenda"] += 2 if nota["agenda"] else 1
    if "código" in por:
        nota["notificações"] += 3
    rem = (remetente or "").lower()
    info_rem = nome_do_email(rem) if "@" in rem else {"entidade": remetente, "tipo": None}
    automatico = bool(re.search(r"no-?reply|nao-?responda|notifica|newsletter|mailer|news@|info@|marketing", rem))
    if automatico:
        nota["notificações"] += 1; nota["promoções"] += 0.5
    tot = sum(nota.values())
    prob = {c: (v / tot if tot else 0.0) for c, v in nota.items()}
    mod = _modelo(engine, corpo, texto) if engine is not None else None
    if mod is not None:
        r = mod["categoria"]
        w = 0.5 if tot else 1.0                       # sem sinais, vale o modelo
        prob = {c: w * r["distribution"][c] + (1 - w) * prob[c] for c in CATEGORIAS}
    if not tot and engine is None:
        prob["pessoal" if not automatico else "notificações"] = 1.0
    categoria = max(prob, key=prob.get)

    # ---- segurança (só com o modelo) ----
    seguranca = None
    if mod is not None:
        r = mod["seguranca"]
        seguranca = {"rotulo": r["answer"], "p": round(float(r["probability"]), 3)}
        if r["answer"] != "seguro" and r["probability"] >= 0.7:
            categoria = "segurança"

    # ---- prioridade ----
    pts = 20
    datas = sorted(d.valor for d in por.get("data", []) if d.valor)
    prazo = datas[0] if datas else None
    if any(v.lower() in rem or (info_rem.get("entidade") or "").lower() == v.lower() for v in vips if v):
        pts += 30; motivos.append("remetente importante")
    if re.search(URGENTE, s):
        pts += 40; motivos.append("pede urgência")
    if prazo:
        dias = (_dt.date.fromisoformat(prazo) - hoje).days
        if dias <= 0:
            pts += 30; motivos.append("prazo hoje")
        elif dias == 1:
            pts += 22; motivos.append("prazo amanhã")
        elif dias <= 3:
            pts += 12; motivos.append(f"prazo em {dias} dias")
    pergunta = bool(re.search(PERGUNTA, s)) and not automatico
    if pergunta:
        pts += 10; motivos.append("espera resposta")
    if "código" in por:
        pts += 20; motivos.append("código temporário")
    if categoria == "financeiro" and prazo:
        pts += 10
    if categoria == "promoções":
        pts -= 25
    if automatico and categoria != "notificações":
        pts -= 10
    if categoria == "segurança":
        pts = max(pts, 75); motivos.append("possível ameaça")
    pts = max(0, min(100, pts))
    if mod is not None:                           # urgência ordinal do modelo (SCORE, como no Jev), misturada às regras
        niv = NIVEIS
        dist = mod["urgencia"].get("distribution") or {}
        if dist:
            esperado = sum(dist.get(n, 0) * v for n, v in zip(niv, (10, 35, 60, 85)))
            pts = int(round(0.6 * pts + 0.4 * esperado))
            motivos.append(f"modelo: {max(dist, key=dist.get)}")
    prioridade = "urgente" if pts >= 70 else "alta" if pts >= 50 else "normal" if pts >= 25 else "baixa"

    # ---- pedido (a mensagem pede algo a quem recebe?) ----
    pedido = None
    if categoria not in ("segurança", "promoções") and not automatico:
        pedido = extrair_pedido(texto, assunto=assunto, remetente=info_rem.get("entidade") or remetente,
                                prazo=prazo, valores=[d.valor or d.texto for d in por.get("valor", [])],
                                trechos=dados, mod=mod)

    # ---- ações e destino no app ----
    acoes, destino = [], None
    if categoria == "segurança":
        acoes, destino = ["não clicar em links", "apagar ou denunciar"], "quarentena"
    else:
        if pergunta:
            acoes.append("responder")
        if categoria == "agenda" or "link de reunião" in por or ("data" in por and "hora" in por and categoria != "financeiro"):
            acoes.append("agendar"); destino = "card de agenda"
        if categoria == "financeiro" and ("boleto" in por or "valor" in por or "chave pix" in por):
            acoes.append("pagar"); destino = destino or "financeiro (tarefa com vencimento)"
        if pedido and "criar tarefa" not in acoes:
            acoes.append("criar tarefa"); destino = destino or ("tarefa com prazo" if prazo else "tarefa")
        if "rastreio" in por or categoria == "compras e entregas":
            acoes.append("acompanhar entrega"); destino = destino or "entregas"
        if "código" in por:
            acoes.append("usar código"); destino = destino or "notificação passageira"
        if categoria == "promoções":
            acoes.append("arquivar"); destino = destino or "promoções (resumo semanal)"
        if not acoes:
            acoes.append("ler depois" if prioridade in ("normal", "alta") else "arquivar")
            destino = destino or ("caixa de entrada" if prioridade != "baixa" else "arquivo")

    def _v(c):
        return [d.valor or d.texto for d in por.get(c, [])]
    entidades = [{"nome": d.valor, "tipo": d.extra.get("tipo")} for d in por.get("entidade", [])]
    if info_rem.get("entidade"):
        entidades.insert(0, {"nome": info_rem["entidade"], "tipo": info_rem.get("tipo"), "papel": "remetente"})
    primeira = re.split(r"(?<=[.!?])\s+|\n", texto.strip())[0][:160]
    return {"categoria": categoria, "categorias": {c: round(p, 3) for c, p in sorted(prob.items(), key=lambda x: -x[1])},
            "prioridade": prioridade, "nota": pts, "motivos": motivos, "acoes": acoes, "destino": destino,
            "prazo": prazo, "seguranca": seguranca, "resumo": primeira, "pedido": pedido,
            "dados": {k: v for k, v in {"datas": _v("data"), "horas": _v("hora"), "valores": _v("valor"),
                                         "boleto": _v("boleto"), "rastreio": _v("rastreio"), "codigo": _v("código"),
                                         "reuniao": _v("link de reunião"), "pix": _v("chave pix"),
                                         "telefones": _v("telefone"), "emails": _v("endereço de e-mail"),
                                         "links": _v("url")}.items() if v},
            "entidades": entidades}
