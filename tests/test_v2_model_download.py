"""Download do modelo (F): servidor HTTP local servindo o .zip de um modelo de mentira."""
import http.server
import io
import json
import os
import socketserver
import threading
import time
import zipfile

import pytest
import yaml

from djorge_local import model_download as MD

META = {"identity": {"name": "DOFL", "family": "DJORGE"}, "djorge_version": "2.0.0",
        "dataset_version": "receita-v2", "tiny_config": {"vocab": 4096}, "created": "2026-10-30"}


def make_zip(evil=False, pad=0):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_STORED) as z:
        z.writestr("dofl-v2/metadata/djorge.json", json.dumps(META))
        z.writestr("dofl-v2/decision/core.bin", os.urandom(pad) if pad else b"x" * 1000)
        if evil:
            z.writestr("../fora.txt", "ruim")
    return b.getvalue()


class Srv:
    def __init__(self):
        self.files, self.delay = {}, 0.0
        me = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                data = me.files.get(self.path)
                if data is None:
                    self.send_response(404); self.end_headers(); return
                start = 0
                rng = self.headers.get("Range")
                if rng:
                    start = int(rng.split("=")[1].split("-")[0])
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
                else:
                    self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Length", str(len(data) - start))
                self.end_headers()
                try:
                    for i in range(start, len(data), 65536):
                        self.wfile.write(data[i:i + 65536])
                        if me.delay:
                            time.sleep(me.delay)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        socketserver.ThreadingTCPServer.allow_reuse_address = True
        self.httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def url(self, p):
        return f"http://127.0.0.1:{self.httpd.server_address[1]}{p}"


class FakeModels:
    def __init__(self):
        self.preloaded, self.state, self._loaded = [], {}, []

    def preload(self, names=None):
        self.preloaded.append(names)

    def loaded(self):
        return self._loaded

    def unload(self, n):
        pass


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DJORGE_DATA_DIR", str(tmp_path / "dados"))
    srv = Srv()
    cfgp = tmp_path / "config.yaml"
    cfgp.write_text(yaml.safe_dump({"timezone": "America/Sao_Paulo", "models": {"flash": {"enabled": True, "path": ""}}}), encoding="utf-8")
    cfg = {"_path": str(cfgp), "_dir": str(tmp_path), "models": {"flash": {"enabled": True, "path": ""}}}
    yield srv, cfg, tmp_path
    srv.httpd.shutdown()


def test_info_download_config(env):
    srv, cfg, tmp = env
    srv.files["/m.zip"] = make_zip()
    cfg["model_source"] = srv.url("/m.zip")
    fm = FakeModels()
    d = MD.ModelDownloader(cfg, fm)
    i0 = d.info()
    assert i0["installed"] is False and i0["state"] == "ausente" and i0["path"] is None
    assert i0["source"] == srv.url("/m.zip") and i0["name"] == "DOFL V2"
    seen = []
    d.start()
    while d._thread.is_alive():
        seen.append(d.info()["progress"]); time.sleep(0.005)
    i1 = d.info()
    assert i1["error"] is None, i1["error"]
    assert i1["installed"] and i1["state"] == "instalado" and i1["progress"] == 1.0
    assert i1["bytes"] == i1["total"] == len(srv.files["/m.zip"])
    assert all(0 <= p <= 1 for p in seen)
    target = tmp / "dados" / "modelos" / "flash"
    assert (target / "metadata" / "djorge.json").is_file()
    assert cfg["models"]["flash"]["path"] == str(target)
    saved = yaml.safe_load(open(cfg["_path"], encoding="utf-8"))
    assert saved["models"]["flash"]["path"] == str(target) and saved["timezone"] == "America/Sao_Paulo"
    assert fm.preloaded == [["flash"]]
    det = i1["details"]
    assert det["produto"] == "DOFL" and det["versao"] == "2.0.0" and det["vocabulario"] == 4096
    assert det["data"] == "2026-10-30" and "DOFL" in det["texto"]
    r = d.remove()
    assert not r["installed"] and r["state"] == "ausente" and not target.exists()
    assert yaml.safe_load(open(cfg["_path"], encoding="utf-8"))["models"]["flash"]["path"] == ""


def test_progress_cancel_and_resume(env):
    srv, cfg, tmp = env
    srv.files["/big.zip"] = make_zip(pad=3_000_000)
    srv.delay = 0.01
    cfg["model_source"] = srv.url("/big.zip")
    d = MD.ModelDownloader(cfg, FakeModels())
    d.start()
    t0 = time.time()
    while d.info()["bytes"] < 300_000 and time.time() - t0 < 20:
        time.sleep(0.01)
    mid = d.info()
    assert mid["state"] == "baixando" and 0 < mid["progress"] < 1 and mid["total"] == len(srv.files["/big.zip"])
    c = d.cancel()
    assert c["state"] == "ausente" and not c["installed"] and c["error"] is None
    part = tmp / "dados" / "modelos" / ".flash.parcial" / "m.zip.part"
    assert part.is_file() and part.stat().st_size > 0
    have = part.stat().st_size
    srv.delay = 0
    d.start(); d.wait(30)
    i = d.info()
    assert i["installed"], i["error"]
    assert i["bytes"] == i["total"] and have < i["total"]


def test_malicious_zip_refused(env):
    srv, cfg, tmp = env
    srv.files["/mal.zip"] = make_zip(evil=True)
    cfg["model_source"] = srv.url("/mal.zip")
    d = MD.ModelDownloader(cfg, FakeModels())
    d.start(); d.wait(30)
    i = d.info()
    assert i["state"] == "erro" and "inseguro" in i["error"] and not i["installed"]
    assert not (tmp / "dados" / "modelos" / ".flash.parcial" / "fora.txt").exists()
    assert not (tmp / "dados" / "modelos" / "fora.txt").exists()
    assert cfg["models"]["flash"]["path"] == ""


def test_no_disk_space(env, monkeypatch):
    srv, cfg, tmp = env
    srv.files["/m.zip"] = make_zip()
    cfg["model_source"] = srv.url("/m.zip")
    monkeypatch.setattr(MD, "disk_free", lambda p: 1000)
    d = MD.ModelDownloader(cfg, FakeModels())
    d.start(); d.wait(30)
    i = d.info()
    assert i["state"] == "erro" and "espaço insuficiente" in i["error"] and not i["installed"]


def test_not_a_model_and_source_file(env, monkeypatch, tmp_path):
    srv, cfg, tmp = env
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("qualquer/coisa.txt", "oi")
    srv.files["/x.zip"] = b.getvalue()
    src = tmp_path / "modelo-fonte.txt"
    src.write_text("# comentário\nflash=" + srv.url("/x.zip") + "\n", encoding="utf-8")
    monkeypatch.setattr(MD, "_source_files", lambda: [src])
    d = MD.ModelDownloader(cfg, FakeModels())
    assert d.info()["source"] == srv.url("/x.zip")
    d.start(); d.wait(30)
    i = d.info()
    assert i["state"] == "erro" and "djorge.json" in i["error"]


def test_safe_extract_variants(tmp_path):
    for name in ("/abs.txt", "a/../../b.txt", "C:/win.txt", "..\\c.txt"):
        zp = tmp_path / "z.zip"
        with zipfile.ZipFile(zp, "w") as z:
            z.writestr(zipfile.ZipInfo(name), "x")
        with pytest.raises(ValueError):
            MD.safe_extract(zp, tmp_path / "out")
