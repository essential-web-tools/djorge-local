"""Motor (lote, cache, tokenização, truncamento, política de lote), escada de quantização, catálogo de modelos,
instalar de arquivo, início silencioso e status do dispositivo."""
import importlib.util
import json
import threading
import time
import zipfile
from pathlib import Path

import pytest

from djorge.runtime.semantic import SemReq
from djorge_local import motor as M


class FakeSer:
    def __init__(self):
        self.calls = 0

    def _enc(self, text, offsets=False):
        self.calls += 1
        ids = [ord(c) for c in text]
        return ids, ([(i, i + 1) for i in range(len(text))] if offsets else None)


class FakeEngine:
    def __init__(self, delay=0.0):
        self.calls, self.delay, self.ser = [], delay, FakeSer()
        self.meta, self.model_id, self.batch_size, self.stats = {}, "fake", 16, {"forward_calls": 0}
        self.lock = threading.Lock()

    def run(self, reqs, tenant_adapter=None, timings=None):
        with self.lock:
            self.calls.append([r.source for r in reqs])
            time.sleep(self.delay)
            out = []
            for r in reqs:
                self.ser._enc(r.source, True)
                out.append({"answer": len(r.source) % 2 == 0, "probability": 0.9, "src": r.source,
                            "kind": r.kind, "opts": list(r.options)})
            return out


def test_cache_copia_e_ttl():
    e = FakeEngine()
    mo = M.Motor(e, janela_ms=0, ttl_s=0.2)
    a = mo.run([SemReq("NOUL", "oi", options=["x"])])[0]
    a["answer"] = "sujo"
    b = mo.run([SemReq("NOUL", "oi", options=["x"])])[0]
    assert b["answer"] is True and len(e.calls) == 1
    assert mo.metricas()["cache"]["acertos"] == 1
    mo.run([SemReq("NOUL", "oi", options=["y"])])        # outra opção = outra pergunta
    assert len(e.calls) == 2
    time.sleep(0.25)
    mo.run([SemReq("NOUL", "oi", options=["x"])])
    assert len(e.calls) == 3


def test_repetidos_no_mesmo_lote_vao_uma_vez():
    e = FakeEngine()
    mo = M.Motor(e, janela_ms=0)
    r = mo.run([SemReq("NOUL", "a", options=["x"])] * 3 + [SemReq("CHOICE", "a", options=["p", "q"])])
    assert len(r) == 4 and e.calls == [["a", "a"]]
    assert mo.metricas()["repetidos_no_lote"] == 2


def test_lote_automatico_junta_concorrentes():
    e = FakeEngine(delay=0.05)
    mo = M.Motor(e, janela_ms=5)
    out = {}

    def w(i):
        out[i] = mo.run([SemReq("NOUL", f"texto {i}", options=["x"])])[0]["src"]
    th = [threading.Thread(target=w, args=(i,)) for i in range(8)]
    [t.start() for t in th]
    [t.join() for t in th]
    assert out == {i: f"texto {i}" for i in range(8)}          # cada um recebe a sua resposta
    assert len(e.calls) < 8 and sum(len(c) for c in e.calls) == 8
    assert mo.metricas()["media_por_lote"] > 1


def test_erro_chega_a_todos():
    class Ruim(FakeEngine):
        def run(self, reqs, **k):
            raise RuntimeError("quebrou")
    mo = M.Motor(Ruim(), janela_ms=0)
    with pytest.raises(RuntimeError):
        mo.run([SemReq("NOUL", "a", options=["x"])])
    assert mo._ocupado is False


def test_truncamento_so_classificacao():
    e = FakeEngine()
    mo = M.Motor(e, janela_ms=0, max_chars=200)
    longo = "começo importante " + "palavra " * 300 + "assinatura final"
    mo.run([SemReq("CHOICE", longo, options=["a", "b"]), SemReq("resolve", longo, mode="ENTITY", target="x")])
    enviados = e.calls[0]
    curto = [s for s in enviados if len(s) <= 200]
    assert len(curto) == 1 and curto[0].startswith("começo importante") and curto[0].endswith("assinatura final")
    assert longo in enviados                                   # resolve vai inteiro (posições dos trechos)
    assert M.encurtar("a   b\n\n\n\nc", 0) == "a b\n\nc"


def test_cache_de_tokenizacao_compartilhado():
    e = FakeEngine()
    mo = M.Motor(e, janela_ms=0)
    mo.run([SemReq("NOUL", "mesmo texto", options=["x"]), SemReq("CHOICE", "mesmo texto", options=["a", "b"])])
    assert e.ser.calls == 1                                    # tokenizado uma vez para as duas perguntas
    assert mo.metricas()["cache_tokenizacao"]["acertos"] >= 1


def test_engine_compat_e_recarregar_apos_ocioso():
    e = FakeEngine()
    cargas = []
    mo = M.Motor(None, carregar=lambda: (cargas.append(1), mo.trocar(e))[1], janela_ms=0)
    assert mo.engine is mo                                     # getattr(z.semantic, "engine", ...) cai no Motor
    mo.run([SemReq("NOUL", "a", options=["x"])])
    assert cargas == [1] and mo.model_id == "fake"
    mo.soltar()
    assert not mo.carregado()
    mo.run([SemReq("NOUL", "a", options=["x"])])               # do cache: não recarrega
    assert cargas == [1]
    mo.run([SemReq("NOUL", "b", options=["x"])])
    assert cargas == [1, 1]


def _eng_pacote(tmp_path, quant, inv=None):
    (tmp_path / "onnx").mkdir(parents=True, exist_ok=True)
    info = {"files": {"int8": "onnx/encoder.int8.onnx"}, "quant": quant}
    if inv is not None:
        info["lote_invariante"] = inv
    (tmp_path / "onnx" / "encoder.json").write_text(json.dumps(info))
    e = FakeEngine()
    e.meta = {"onnx": {"file": "onnx/encoder.int8.onnx"}}
    e.artifact_path = str(tmp_path)
    return e


def test_politica_lote(tmp_path):
    e = _eng_pacote(tmp_path / "a", "all")
    assert M.politica_lote(e) is False and e.batch_size == 1   # int8 dinâmico: resposta depende do lote
    e = _eng_pacote(tmp_path / "b", "wo8")
    assert M.politica_lote(e) is True and e.batch_size == 16
    e = _eng_pacote(tmp_path / "c", "all", inv=True)
    assert M.politica_lote(e) is True
    assert M.politica_lote(FakeEngine()) is True               # PyTorch / sem onnx


def test_model_info_traz_motor(tmp_path, monkeypatch):
    monkeypatch.setenv("DJORGE_DATA_DIR", str(tmp_path))
    from djorge_local.model_download import ModelDownloader
    art = tmp_path / "m"
    (art / "metadata").mkdir(parents=True)
    (art / "metadata" / "djorge.json").write_text(json.dumps({"onnx": {"quant": "wo8"}}))

    class FM:
        engine = {"flash": "ONNX int8"}

        def loaded(self):
            return ["flash"]

        def motor_info(self, n):
            return {"ms_por_pergunta_modelo": 21.5, "cache": {"acertos": 3}}
    cfg = {"models": {"flash": {"path": str(art)}}}
    i = ModelDownloader(cfg, FM()).info()
    assert i["motor"] == "ONNX int8" and i["ms_medio"] == 21.5 and i["cache"] == {"acertos": 3}
    assert i["quantizacao"] == "int8 nos pesos (CPU)"


# ---------------------------------------------------------------------------- pacote / catálogo --
def _pacote(dirp: Path, onnx=True):
    (dirp / "metadata").mkdir(parents=True)
    (dirp / "metadata" / "djorge.json").write_text(json.dumps({"product": "Teste"}))
    if onnx:
        (dirp / "onnx").mkdir()
        (dirp / "onnx" / "encoder.json").write_text(json.dumps({"files": {"int8": "onnx/encoder.int8.onnx"}}))
        (dirp / "onnx" / "encoder.int8.onnx").write_bytes(b"x" * 10)
        (dirp / "local_profiles.json").write_text("{}")
    return dirp


def _zip(src: Path, zp: Path):
    with zipfile.ZipFile(zp, "w") as z:
        for f in src.rglob("*"):
            if f.is_file():
                z.write(f, "pacote/" + str(f.relative_to(src)))
    return zp


class FakeModels:
    def __init__(self):
        self.state, self.motores, self.unloads, self.preloads = {}, {}, [], []

    def loaded(self):
        return []

    def unload(self, n):
        self.unloads.append(n)

    def preload(self, names=None):
        self.preloads.append(names)


def test_instalar_de_arquivo(tmp_path, monkeypatch):
    monkeypatch.setenv("DJORGE_DATA_DIR", str(tmp_path / "dados"))
    from djorge_local.model_download import ModelDownloader
    cfg = {"models": {"flash": {"path": ""}}}
    fm = FakeModels()
    d = ModelDownloader(cfg, fm, save=lambda: None)
    ruim = _zip(_pacote(tmp_path / "ruim", onnx=False), tmp_path / "ruim.zip")
    with pytest.raises(ValueError):
        d.instalar_de_arquivo(ruim)
    bom = _zip(_pacote(tmp_path / "bom"), tmp_path / "bom.zip")
    i = d.instalar_de_arquivo(bom)
    assert i["installed"] and Path(cfg["models"]["flash"]["path"]).name == "flash"
    assert fm.preloads == [["flash"]]


def test_catalogo(tmp_path, monkeypatch):
    monkeypatch.setenv("DJORGE_DATA_DIR", str(tmp_path / "dados"))
    from djorge_local.model_download import Catalogo
    cfg = {"models": {"flash": {"path": ""}}}
    fm = FakeModels()
    c = Catalogo(cfg, fm, save=lambda: None)
    assert c.listar()[0]["padrao"] and c.listar()[0]["estado"] == "ausente"
    local = _pacote(tmp_path / "meu_modelo")
    it = c.adicionar(str(local), nome="Meu modelo")
    assert it["estado"] == "instalado" and it["tipo"] == "pasta" and not it["em_uso"]
    z = c.adicionar(str(_zip(_pacote(tmp_path / "outro"), tmp_path / "outro.zip")))
    assert z["estado"] == "instalado" and Path(z["path"]).parent.name == "modelos"
    assert "outro" not in cfg["models"]                       # não vira um segundo modelo carregado
    u = c.usar(it["id"])
    assert u["em_uso"] and cfg["models"]["flash"]["path"] == str(local)
    assert fm.unloads == ["flash"] and fm.preloads == [["flash"]]
    with pytest.raises(ValueError):
        c.remover(it["id"])                                   # em uso
    c.usar(z["id"])
    c.remover(it["id"])
    assert local.is_dir()                                     # pasta da pessoa nunca é apagada
    with pytest.raises(ValueError):
        c.adicionar("nada-disso")
    assert [i["id"] for i in c.listar()] == ["dofl-v2-int8", z["id"]]


# ------------------------------------------------------------------------------ início / dispositivo --
def test_inicio_adiado():
    from djorge_local import leveza
    feito = []
    ini = leveza.InicioAdiado(lambda: feito.append("ini"), lambda: feito.append("mod"), adiar_s=60, modelo_s=0)
    assert feito == []
    leveza.atividade()                                        # primeiro uso liga as integrações
    for _ in range(50):
        if feito:
            break
        time.sleep(0.01)
    assert feito == ["ini"]
    ini.ja(modelo=True)
    ini.ja(modelo=True)
    assert feito == ["ini", "mod"]
    ini.cancelar()
    leveza.marcar_inicio(False)
    assert not leveza.inicio_silencioso()


def test_dispositivo_resumo(monkeypatch):
    from djorge_local import dispositivo as D
    st = {"bateria": {"tem_bateria": True, "percentual": 12, "carregando": False, "na_tomada": False},
          "energia": {"economia": True}, "rede": {"online": True, "tipo": "wifi"}, "cpu": {"nucleos": 4},
          "memoria": {"livre_mb": 3000}, "disco": {"livre_gb": 50}, "horario": D.horario(),
          "foco": {"nao_perturbe": True}, "tela": {"bloqueada": None}}
    r = D.resumo(st)
    assert "bateria 12%" in r["texto"] and r["evitar"]["baixar_modelo"] and r["evitar"]["notificar"]
    assert any("Bateria baixa" in a for a in r["avisos"])
    monkeypatch.setattr(D, "rede", lambda: {"online": True, "tipo": None})
    s = D.status(forcar=True)
    assert set(s) >= {"bateria", "rede", "memoria", "disco", "horario", "foco", "tela"}
    assert D.status() is not s and D.status()["lido_em"] == s["lido_em"]   # cache curto


# ------------------------------------------------------------------------------- quantização --
@pytest.fixture(scope="module")
def tiny(tmp_path_factory):
    pytest.importorskip("torch")
    for mod in ("onnx", "onnxscript", "onnxruntime"):
        if importlib.util.find_spec(mod) is None:
            pytest.skip(f"falta {mod}")
    out = str(tmp_path_factory.mktemp("motor") / "tiny")
    from djorge.training.trainer import main
    main(["--backbone", "tiny", "--out", out, "--steps", "3", "--save-every", "1000", "--data-scale", "0.05"])
    return out


def test_escada_wo8_lote_invariante(tiny, tmp_path):
    from djorge_local.otimizar import converter
    rep = converter(tiny, tmp_path / "p", ladder=(("all", "wo8"),),
                    paridade={"agreement": 1.0, "mean_abs_prob_diff": 0.00015})
    info = json.load(open(tmp_path / "p" / "onnx" / "encoder.json"))
    assert rep["passed"] and rep["lora_folded"] > 0
    wo8 = next(m for m in rep["ladder"] if m["mode"] == "wo8")
    assert wo8["lote_dp"] == 0.0                              # só pesos em int8: resposta não depende do lote
    if rep["chosen"] == "wo8":
        assert info["lote_invariante"] is True
