"""Modelo leve (rodada 2, parte F): perfil ONNX x PyTorch, conversão única em segundo plano, caminho ONNX sem torch,
pausar/retomar/liberar memória, descarregar ocioso e liga/desliga da API local."""
import importlib.util
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _fake_artifact(p: Path, onnx=False, profiles=False):
    (p / "metadata").mkdir(parents=True, exist_ok=True)
    (p / "metadata" / "djorge.json").write_text(json.dumps({"model_id": "t", "model_config": {}}), encoding="utf-8")
    if onnx:
        (p / "onnx").mkdir(exist_ok=True)
        (p / "onnx" / "encoder.int8.onnx").write_bytes(b"x")
        (p / "onnx" / "encoder.json").write_text(json.dumps({"files": {"int8": "onnx/encoder.int8.onnx"}}))
    if profiles:
        (p / "local_profiles.json").write_text(json.dumps({"profiles": {"cpu": {"onnx": "int8"}}, "default": "cpu"}))
    return p


@pytest.fixture()
def dados(tmp_path, monkeypatch):
    monkeypatch.setenv("DJORGE_DATA_DIR", str(tmp_path / "dados"))
    from djorge_local import otimizar as O
    O.OTIMIZADOR._st.clear()
    yield tmp_path / "dados"
    O.OTIMIZADOR.wait(5)
    O.OTIMIZADOR._st.clear()
    O.OTIMIZADOR.converter = O.converter


def _cfg(path=""):
    return {"models": {"flash": {"enabled": True, "path": str(path)}}, "device": "cpu",
            "api": {"enabled": False, "host": "127.0.0.1", "ports": {}}, "server": {"token": "tok"}}


# ----------------------------------------------------------------------------------------- perfil --
def test_pick_profile_com_e_sem_onnx(tmp_path):
    pytest.importorskip("onnxruntime")
    from djorge_local.models import pick_profile
    assert pick_profile(_fake_artifact(tmp_path / "a"), {})["engine"] == "pytorch"
    p = pick_profile(_fake_artifact(tmp_path / "b", onnx=True, profiles=True), {"device": "cpu"})
    assert p["engine"] == "onnx" and p["onnx"] == "int8" and p["providers"] == ["CPUExecutionProvider"]
    assert pick_profile(_fake_artifact(tmp_path / "c", onnx=True), {})["engine"] == "onnx"     # sem profiles
    assert pick_profile(_fake_artifact(tmp_path / "d", profiles=True), {})["engine"] == "pytorch"  # sem onnx/


# ------------------------------------------------------------------------- conversão (exportador falso) --
def test_conversao_chamada_no_artefato_sem_onnx(dados):
    from djorge_local import otimizar as O
    from djorge_local.model_download import ModelDownloader, models_root
    from djorge_local.models import Models
    art = _fake_artifact(models_root() / "flash")
    libera, chamado = threading.Event(), []

    def falso(src, out, progresso):
        chamado.append(Path(src))
        progresso(0.5, "Gerando o encoder ONNX…")
        assert libera.wait(10)
        _fake_artifact(Path(out), onnx=True, profiles=True)
        return {"passed": True}
    O.OTIMIZADOR.converter = falso
    O_pode = O.pode_converter
    O.pode_converter = lambda: (True, "")
    try:
        cfg = _cfg(art)
        m = Models(cfg)
        recarregou = []
        m.preload = lambda names=None: recarregou.append(names)
        with pytest.raises(RuntimeError, match="Otimizando"):
            m.get("flash")                       # não carrega PyTorch junto com a conversão
        assert m.state["flash"] == "otimizando"
        i = ModelDownloader(cfg, m).info()
        assert i["state"] == "otimizando" and i["mensagem"] == "Otimizando o modelo para este computador…"
        assert i["progress"] == 0.5 and i["formato"].startswith("PyTorch")
        libera.set()
        O.OTIMIZADOR.wait(10)
        assert chamado == [art]
        assert O.eh_pacote_onnx(art) and not O.precisa_otimizar(art)   # pasta trocada pelo pacote ONNX
        assert recarregou == [["flash"]]
        i = ModelDownloader(cfg, m).info()
        assert i["state"] == "instalado" and i["formato"] == "ONNX int8"
    finally:
        O.pode_converter = O_pode


def test_sem_conversao_possivel_pede_pacote_onnx(dados, monkeypatch):
    from djorge_local import otimizar as O
    from djorge_local.model_download import ModelDownloader, models_root
    from djorge_local.models import Models
    monkeypatch.setenv("DJORGE_SEM_CONVERSAO", "1")
    art = _fake_artifact(models_root() / "flash")
    cfg = _cfg(art)
    m = Models(cfg)
    st = O.OTIMIZADOR.iniciar(art)
    assert st["state"] == "indisponivel" and "pacote leve" in st["mensagem"]
    i = ModelDownloader(cfg, m).info()
    assert i["state"] == "instalado" and "ONNX" in i["mensagem"]


def test_pasta_escolhida_pela_pessoa_nunca_e_convertida(dados, tmp_path):
    from djorge_local import otimizar as O
    from djorge_local.models import Models
    art = _fake_artifact(tmp_path / "meu_treino")
    m = Models(_cfg(art))
    m._maybe_optimize("flash", art)
    assert O.OTIMIZADOR.info(art) == {} and not O.OTIMIZADOR.ocupado()


# --------------------------------------------------------------------------------- sem torch --
def test_imports_do_caminho_onnx_com_torch_bloqueado():
    code = ("import sys; sys.modules['torch'] = None\n"
            "import djorge, djorge.server, djorge.runtime.semantic, djorge.runtime.onnx_backend\n"
            "import djorge_local.models, djorge_local.leveza, djorge_local.otimizar, djorge_local.model_download\n"
            "from djorge.runtime.onnx_backend import NumpyDecision, read_safetensors_numpy\n"
            "print('ok')")
    r = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and "ok" in r.stdout, r.stderr[-2000:]


def test_cabecas_numpy_iguais_ao_pytorch():
    torch = pytest.importorskip("torch")
    import numpy as np
    from djorge.model.djorge_model import DjorgeModel
    from djorge.model.serialize import Row
    from djorge.runtime.onnx_backend import NumpyDecision

    class BB(torch.nn.Module):
        def __init__(self):
            super().__init__()
            from types import SimpleNamespace
            self.config = SimpleNamespace(hidden_size=16, num_hidden_layers=2)
            self.e = torch.nn.Embedding(4, 16)

        def get_input_embeddings(self):
            return self.e
    torch.manual_seed(0)
    m = DjorgeModel(BB(), d=32, adapter_layers=1, heads=2, use_lora=False).eval()
    sd = {k: v.detach().numpy() for k, v in m.state_dict().items() if not k.startswith("backbone.")}
    npm = NumpyDecision(sd, None, {"hidden": 16, "layers": 2}, {})
    X = torch.randn(1, 9, 32)
    row = Row(ids=[1] * 9, roles=[0] * 9, src_tok_idx=[1, 2, 3], src_offsets=[(0, 1)] * 3,
              opt_spans=[(4, 5), (5, 6), (6, 8)], end_idx=8)
    Xn = X.numpy()
    with torch.no_grad():
        assert np.allclose(m.token_logits(X, [row])[0].numpy(), npm.token_logits(Xn, [row])[0], atol=1e-5)
        assert np.allclose(m.slot_logits(X, 0, row).numpy(), npm.slot_logits(Xn, 0, row), atol=1e-5)
        assert abs(float(m.binary_logit(X, 0, row)) - npm.binary_logit(Xn, 0, row)) < 1e-5
        assert np.allclose(m.ordinal_logprobs(X, 0, row).numpy(), npm.ordinal_logprobs(Xn, 0, row), atol=1e-5)


@pytest.fixture(scope="module")
def tiny(tmp_path_factory):
    pytest.importorskip("torch")
    out = str(tmp_path_factory.mktemp("leve") / "tiny")
    from djorge.training.trainer import main
    main(["--backbone", "tiny", "--out", out, "--steps", "3", "--save-every", "1000", "--data-scale", "0.05"])
    return out


def test_pacote_onnx_real_roda_sem_torch(tiny, tmp_path):
    for mod in ("onnx", "onnxscript", "onnxruntime"):
        if importlib.util.find_spec(mod) is None:
            pytest.skip(f"falta {mod}")
    from djorge_local.otimizar import converter, eh_pacote_onnx
    pac = tmp_path / "pacote"
    rep = converter(tiny, pac)
    assert rep["passed"] and eh_pacote_onnx(pac)
    assert not (pac / "backbone").exists()
    code = ("import sys, os, json; sys.modules['torch'] = None\n"
            "os.environ['DJORGE_ONNX'] = 'int8'; os.environ['DJORGE_ONNX_PROVIDERS'] = 'CPUExecutionProvider'\n"
            "from djorge import Djorge\n"
            "z = Djorge(artifact=sys.argv[1])\n"
            "r = z.execute(sys.argv[2])['result']\n"
            "print(json.dumps({'a': r['answer'], 'p': r['probability'], 'onnx': z.semantic.meta.get('onnx', {}).get('file')}))")
    q = 'CHOICE ["cobrança", "outro"] FROM "O boleto vence amanhã" ASK "Assunto?"'
    r = subprocess.run([sys.executable, "-c", code, str(pac), q], cwd=str(ROOT), capture_output=True, text=True,
                       timeout=180)
    assert r.returncode == 0, r.stderr[-2000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["a"] in ("cobrança", "outro") and out["onnx"].endswith("int8.onnx")


# --------------------------------------------------------------------- pausar / retomar / memória --
class _Conn:
    def __init__(self):
        self.parado = False

    def stop(self):
        self.parado = True


class _Sec:
    def __init__(self):
        self.ativo, self.log = True, []

    def pausar(self):
        self.log.append("pausar"); self.ativo = False

    def retomar(self):
        self.log.append("retomar"); self.ativo = True


class _App:
    def __init__(self, models):
        self.cfg = {"connectors": {}, "models": {"flash": {}}}
        self.models = models
        self.conns = {"notifications": _Conn()}
        self.sec = _Sec()
        self.integ_paused = []
        self._svc = {"secretario": self.sec, "integrations": object()}

    def pause_integrations(self, paused=True):
        self.integ_paused.append(paused)


def _models_com_falso():
    from djorge_local.models import Models
    m = Models(_cfg(""))
    m._z["flash"] = object()
    m.engine["flash"] = "ONNX int8"
    m.last_used["flash"] = time.time()
    return m


def test_pausar_retomar_liberar(dados):
    from djorge_local import leveza as L
    m = _models_com_falso()
    app = _App(m)
    r = L.recursos(app)
    assert not r["pausado"] and r["modelo"] == {"carregado": True, "motor": "ONNX int8", "nome": "flash",
                                                "estado": None}
    assert "Conector: notifications" in r["rodando"] and "Secretário" in r["rodando"] and r["memoria_mb"] > 0
    conn = app.conns["notifications"]
    r = L.pausar(app)
    assert r["pausado"] and conn.parado and app.conns == {} and app.integ_paused == [True]
    assert app.sec.log == ["pausar"] and m.in_memory() == [] and m.loaded() == []
    assert r["modelo"]["carregado"] is False and "Secretário" not in r["rodando"]
    r = L.retomar(app)
    assert not r["pausado"] and app.integ_paused == [True, False] and app.sec.log == ["pausar", "retomar"]
    assert m.loaded() == ["flash"] and m.in_memory() == []          # volta no próximo uso, sem pesar agora
    m._z["flash"] = object()
    lib = L.liberar_memoria(app, 0)
    assert lib["descarregados"] == ["flash"] and set(lib) >= {"antes_mb", "depois_mb", "liberado_mb"}


def test_descarregar_ocioso_e_vigia(dados):
    from djorge_local import leveza as L
    m = _models_com_falso()
    assert m.unload_idle(5) == []                                  # usado agora
    m.last_used["flash"] = time.time() - 600
    assert m.unload_idle(5) == ["flash"]
    assert m.in_memory() == [] and m.loaded() == ["flash"] and "ocioso" in m.state["flash"]
    m._z["flash"] = object(); m.last_used["flash"] = time.time() - 3600
    L.salvar_config({"descarregar_apos_min": 30, "iniciar_com_windows": False})
    assert L.ler_config()["descarregar_apos_min"] == 30
    out = L.Vigia(_App(m)).tick()
    assert out["descarregados"] == ["flash"]
    L.salvar_config({"descarregar_apos_min": 0})
    m._z["flash"] = object(); m.last_used["flash"] = 0
    assert L.Vigia(_App(m)).tick()["descarregados"] == []           # 0 = nunca
    with pytest.raises(ValueError):
        L.salvar_config({"descarregar_apos_min": "muito"})


# ------------------------------------------------------------------------------ API local --
def _porta_livre():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    return p


def test_stop_start_api_local(tiny):
    from djorge_local.models import Models
    cfg = _cfg(tiny)
    m = Models(cfg)
    m.get("flash")
    assert m.api_info("flash")["ativa"] is False                    # api.enabled = False
    p = _porta_livre()
    info = m.start_api("flash", p)
    assert info == {"modelo": "flash", "porta": p, "ativa": True, "url": f"http://127.0.0.1:{p}"}
    with urllib.request.urlopen(f"http://127.0.0.1:{p}/health", timeout=5) as r:
        assert r.status == 200
    info = m.stop_api("flash")
    assert info["ativa"] is False and "flash" in m.loaded()         # o modelo continua para o app
    with pytest.raises(OSError):
        urllib.request.urlopen(f"http://127.0.0.1:{p}/health", timeout=2)
    m.unload("flash")
    m.get("flash")
    assert m.api_info("flash")["ativa"] is False                    # desligada continua desligada
    assert m.start_api("flash")["ativa"] is True
    m.stop_api("flash")
