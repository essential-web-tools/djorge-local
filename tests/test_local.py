import json, os, shutil, sys, threading, time, urllib.request
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT.parent / "djorge"))


@pytest.fixture(scope="session")
def art(tmp_path_factory):
    out = str(tmp_path_factory.mktemp("a") / "tiny")
    from djorge.training.trainer import main
    main(["--backbone", "tiny", "--out", out, "--steps", "5", "--save-every", "1000", "--data-scale", "0.1"])
    return out


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("DJORGE_LOCAL_HOME", str(tmp_path))
    import importlib, djorge_local.config as C
    importlib.reload(C)
    return tmp_path


def test_seal_roundtrip_and_tamper(art, tmp_path):
    from djorge_local import crypto
    f = tmp_path / "m.djx"
    man = crypto.seal(art, str(f), "chave-de-teste-123", "origo")
    assert crypto.info(str(f), "chave-de-teste-123")["name"] == "origo"
    with pytest.raises(crypto.BadKey):
        crypto.info(str(f), "chave-errada-0000")
    d = crypto.open_sealed(str(f), "chave-de-teste-123")
    assert (Path(d) / "metadata" / "djorge.json").is_file()
    b = bytearray(f.read_bytes()); b[-50] ^= 1; f.write_bytes(bytes(b))
    with pytest.raises(crypto.BadKey):
        crypto.open_sealed(str(f), "chave-de-teste-123")


def _cfg(home, art, trees):
    import yaml
    (home / "arvores.yaml").write_text(yaml.safe_dump({"trees": trees}, allow_unicode=True), encoding="utf-8")
    sealed = home / "origo.djx"
    from djorge_local import crypto
    crypto.seal(art, str(sealed), "chave-de-teste-123", "origo")
    (home / "config.yaml").write_text(yaml.safe_dump({
        "device": "cpu", "models": {"origo": {"sealed": str(sealed)}, "flash": {"path": art}},
        "server": {"port": 18765}}), encoding="utf-8")
    os.environ["DJORGE_MODEL_KEY"] = "chave-de-teste-123"
    from djorge_local import config as C
    return C.load(str(home / "config.yaml"))


TREES = [{"name": "t", "start": "a", "nodes": {
    "a": {"if": {"contains_any": ["boleto"]}, "branches": {"true": ["p", "c"], "false": "fim"}},
    "p": {"check": "o prazo já venceu", "branches": {"true": "n", "false": "fim"}},
    "c": {"choice": ["cobrança", "outro"], "ask": "assunto?", "branches": {"default": "w"}},
    "n": {"action": "notify", "title": "Vencido", "message": "{title} {text:10}", "then": "fim"},
    "w": {"action": "write_file", "file": "r.txt", "content": "{c.answer}"},
    "fim": {"action": "stop"}}}]


def test_tree_runs_models_clock_and_confirmation(home, art):
    cfg = _cfg(home, art, TREES)
    from djorge_local.app import App
    app = App(cfg)
    r = app.handle({"id": "1", "source": "teste", "title": "Conta", "text": "O boleto vence em 01/01/2020.",
                    "app": "", "sender": ""})
    nodes = [s["node"] for s in r["runs"][0]["trail"]]
    assert nodes[:3] == ["a", "p", "c"] and "n" in nodes and "w" in nodes
    p = next(s for s in r["runs"][0]["trail"] if s["node"] == "p")
    assert p["answer"] is True and p["grounding"] == "temporal_rule"
    w = next(s for s in r["runs"][0]["trail"] if s["node"] == "w")
    assert w["status"] == "ok" and Path(w["file"]).read_text().strip() in ("cobrança", "outro")
    assert app.handle({"id": "2", "source": "teste", "text": "oi", "title": "", "app": "", "sender": ""}
                      )["runs"][0]["trail"][-1]["node"] == "fim"


def test_server_token_and_endpoints(home, art):
    cfg = _cfg(home, art, TREES)
    from djorge_local.app import App, make_handler
    from http.server import ThreadingHTTPServer
    app = App(cfg)
    s = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app)); port = s.server_address[1]
    threading.Thread(target=s.serve_forever, daemon=True).start()
    def call(path, body=None, tok=cfg["server"]["token"]):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode() if body else None,
                                     headers={"X-Token": tok, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, None
    assert call("/api/status", tok="x")[0] == 401
    assert call("/api/status")[1]["trees"] == ["t"]
    code, r = call("/api/event", {"sync": True, "text": "boleto vence 01/01/2020", "title": "x"})
    assert code == 200 and r["runs"][0]["tree"] == "t"
    code, r = call("/api/decide", {"query": 'NOUL "o prazo já venceu" FROM text', "text": "vence em 01/01/2020"})
    assert r["result"]["answer"] is True
    assert call("/api/trees", {"yaml": "trees: [{name: x, start: nope, nodes: {}}]"})[0] == 400
    s.shutdown()


def test_extract_and_render():
    from djorge_local.extract import from_eml, html_to_text
    from djorge_local.tree import render
    e = from_eml(b"Subject: Oi\nFrom: a@b.c\nContent-Type: text/plain\n\ncorpo")
    assert "corpo" in e["text"] and e["title"] == "Oi"
    assert html_to_text("<p>a</p><script>x</script>b").split() == ["a", "b"]
    assert render("{title} {text:3} {n.answer} {__class__}", {"event": {"title": "T", "text": "abcdef"},
                                                              "results": {"n": {"answer": "sim"}}}) == "T abc… sim "


def test_full_api_per_model(home, art):
    import yaml
    (home / "config.yaml").write_text(yaml.safe_dump({"device": "cpu", "models": {"origo": {"path": art}},
                                                      "api": {"ports": {"origo": 18750}}}), encoding="utf-8")
    from djorge_local import config as C
    from djorge_local.models import Models
    cfg = C.load(str(home / "config.yaml"))
    m = Models(cfg); m.get("origo")
    tok = cfg["server"]["token"]
    def call(path, body, t=tok):
        req = urllib.request.Request("http://127.0.0.1:18750" + path, data=json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {t}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, None
    assert call("/v1/classify", {"text": "x", "labels": ["a", "b"]}, "errado")[0] == 401
    code, r = call("/v1/classify", {"text": "O boleto vence amanhã", "labels": ["cobrança", "outro"]})
    assert code == 200 and r["answer"] in ("cobrança", "outro")
    assert call("/v1/tools/time", {"text": "vence amanhã"})[1]["dates"][0]["days_from_now"] == 1
    assert call("/v1/sentinel", {"text": "x"})[0] == 404
    code, r = call("/execute", {"query": "TOTAL AGID limão FROM t", "sources": {"t": "2 limões e 3 limões"}})
    assert r["result"]["value"] == 5


def test_models_tab_endpoints(home, art):
    import yaml
    (home / "config.yaml").write_text(yaml.safe_dump({"device": "cpu", "models": {"origo": {"path": ""}},
                                                      "api": {"ports": {"origo": 18780}}}), encoding="utf-8")
    from djorge_local import config as C
    from djorge_local.app import App, make_handler
    from http.server import ThreadingHTTPServer
    cfg = C.load(str(home / "config.yaml")); app = App(cfg)
    s = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app)); port = s.server_address[1]
    threading.Thread(target=s.serve_forever, daemon=True).start()
    def call(path, body=None):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode() if body is not None else None,
                                     headers={"X-Token": cfg["server"]["token"], "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")
    assert call("/api/models/save", {"name": "origo", "path": "/nao/existe"})[0] == 400
    assert call("/api/models/save", {"name": "Bad Name"})[0] == 400
    code, r = call("/api/models/save", {"name": "origo", "path": art, "load": True})
    assert code == 200
    for _ in range(100):
        st = {m["name"]: m["state"] for m in call("/api/models")[1]["models"]}
        if st["origo"] == "pronto":
            break
        time.sleep(0.2)
    assert st["origo"] == "pronto"
    assert yaml.safe_load((home / "config.yaml").read_text())["models"]["origo"]["path"] == art
    call("/api/models/save", {"name": "extra", "enabled": False})
    assert {m["name"] for m in call("/api/models")[1]["models"]} == {"origo", "flash", "extra"}
    assert call("/api/models/unload", {"name": "origo"})[1]["models"][0]["state"] == "não carregado"
    call("/api/models/remove", {"name": "extra"})
    assert "extra" not in yaml.safe_load((home / "config.yaml").read_text())["models"]
    s.shutdown()


def test_settings_visual_and_sealed_models_are_shredded(home, art):
    import yaml
    (home / "config.yaml").write_text(yaml.safe_dump({"device": "cpu"}), encoding="utf-8")
    os.environ["DJORGE_MODEL_KEY"] = "chave-de-teste-123"
    from djorge_local import config as C
    from djorge_local.app import App
    cfg = C.load(str(home / "config.yaml")); app = App(cfg)
    st = app.settings()
    assert "token" not in st["server"] and st["connectors"]["folder"]["enabled"] is False
    r = app.save_settings({"connectors": {"folder": {"paths": "~/a\n~/b", "poll_s": "5"}}, "max_steps": "30"})
    raw = yaml.safe_load((home / "config.yaml").read_text())
    assert raw["connectors"]["folder"]["paths"] == ["~/a", "~/b"] and raw["max_steps"] == 30 and r == []
    with pytest.raises(ValueError):
        app.save_settings({"max_steps": "muitos"})
    out = app.models.import_folder("origo", art, app.save_config)
    assert Path(out).is_file()
    import djorge_local.models as M
    z = app.models.get("origo")
    assert not M._TMP and not any(Path(d).exists() for d in M._TMP)            # decrypted copy is gone
    assert z.execute('NOUL "x" FROM t', sources={"t": "y"})["result"]["answer"] in (True, False)
