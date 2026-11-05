"""Núcleo v2: cards, comando sem modelo, busca, preferências de interface e rotas (servidor real numa porta livre)."""
import datetime as dt
import json
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DJORGE_LOCAL_HOME", str(tmp_path))
    import importlib
    import yaml
    import djorge_local.config as C
    importlib.reload(C)
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    (tmp_path / "arvores.yaml").write_text("trees: []\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({
        "device": "cpu", "models": {"flash": {"path": ""}}, "server": {"port": port},
        "seguranca": {"enabled": False}}), encoding="utf-8")
    cfg = C.load(str(tmp_path / "config.yaml"))
    from djorge_local.app import App
    a = App(cfg)
    yield a
    a.cards.close()


@pytest.fixture()
def srv(app):
    from djorge_local.app import serve
    httpd = serve(app, block=False)
    base = f"http://127.0.0.1:{app.cfg['server']['port']}"
    tok = app.cfg["server"]["token"]

    def call(path, body=None, token=True, headers=None):
        h = {"Content-Type": "application/json", **(headers or {})}
        if token:
            h["X-Token"] = tok
        req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(), headers=h)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if "json" in r.headers.get("Content-Type", "") else raw)
        except urllib.error.HTTPError as e:
            raw = e.read()
            return e.code, (json.loads(raw) if raw[:1] == b"{" else raw)
    yield call
    httpd.shutdown()


# ------------------------------------------------------------------ cards --
def test_cards_crud_filters_move_agenda(app):
    C = app.cards
    a = C.create({"title": "Reunião com Ana", "kind": "evento", "start": "2026-11-05T14:00", "end": "2026-11-05T15:00",
                  "tags": ["#trabalho"], "people": [{"nome": "Ana", "tipo": "pessoa", "email": "ANA@x.com"}], "priority": 2})
    b = C.create({"title": "Comprar pão", "due": "2026-11-03", "column": "hoje"})
    c = C.create({"title": "Ideias", "kind": "nota", "body": "app de receitas",
                  "source": {"integration": "google", "external_id": "e1", "url": None}})
    assert a["tags"] == ["trabalho"] and a["people"][0]["email"] == "ana@x.com" and a["column"] == "entrada"
    with pytest.raises(ValueError):
        C.create({"title": "x", "kind": "outro"})
    with pytest.raises(ValueError):
        C.create({"title": ""})
    assert [x["id"] for x in C.list(kind="evento")] == [a["id"]]
    assert [x["id"] for x in C.list(column="hoje")] == [b["id"]]
    assert [x["id"] for x in C.list(tag="trabalho")] == [a["id"]]
    assert [x["id"] for x in C.list(person="ana")] == [a["id"]]
    assert [x["id"] for x in C.list(q="receitas")] == [c["id"]]
    assert [x["id"] for x in C.list(source="google")] == [c["id"]]
    assert {x["id"] for x in C.list(source="local")} == {a["id"], b["id"]}
    assert [x["id"] for x in C.list(priority="2+")] == [a["id"]]
    assert {x["id"] for x in C.list(frm="2026-11-04", to="2026-11-30")} == {a["id"]}
    u = C.update(b["id"], {"done": True})
    assert u["done"] and u["column"] == "feito"
    assert [x["id"] for x in C.list(done="true")] == [b["id"]]
    m = C.move(a["id"], "fazendo", order=0.5, start="2026-11-06T10:00")
    assert m["column"] == "fazendo" and m["order"] == 0.5 and m["end"] == "2026-11-06T11:00"
    ag = C.agenda("2026-11-01", "2026-11-10")
    assert [x["id"] for x in ag] == [b["id"], a["id"]]
    ext = C.upsert_external("google", "e1", {"title": "Ideias novas", "column": "entrada"})
    assert ext["id"] == c["id"] and ext["title"] == "Ideias novas"
    C.move(c["id"], "aguardando")
    ext = C.upsert_external("google", "e1", {"title": "Ideias 3", "column": "entrada"})
    assert ext["column"] == "aguardando" and C.get_external("google", "e1")["title"] == "Ideias 3"
    C.delete(c["id"])
    assert C.get(c["id"]) is None
    with pytest.raises(KeyError):
        C.delete(c["id"])


# ------------------------------------------------------------------ uiprefs --
def test_uiprefs_validation_and_parse(app):
    U = app.ui
    assert U.get()["theme"] == "auto"
    with pytest.raises(ValueError):
        U.set({"theme": "roxo"})
    with pytest.raises(ValueError):
        U.set({"css": "body{}"})
    with pytest.raises(ValueError):
        U.set({"kanban_columns": ["feito", "xyz"]})
    assert U.parse("deixa o tema escuro")["patch"] == {"theme": "escuro"}
    assert U.parse("esconde a coluna feito")["patch"]["kanban_columns"] == ["entrada", "hoje", "fazendo", "aguardando"]
    f = U.parse("salva filtro urgentes desta semana", hoje=dt.date(2026, 11, 4))["patch"]["saved_filters"][0]
    assert f["name"] == "urgentes desta semana" and f["filters"] == {"priority": "3", "from": "2026-11-02", "to": "2026-11-08"}
    assert U.parse("abre sempre na agenda")["patch"] == {"default_view": "agenda"}
    assert U.parse("aumenta a fonte")["patch"] == {"font_size": "grande"}
    assert U.parse("desliga a voz")["patch"] == {"voice_enabled": False}
    assert U.parse("comprar pão amanhã") is None
    U.set({"theme": "claro"})
    assert json.loads((Path(U.path)).read_text())["theme"] == "claro"


# ------------------------------------------------------------------ comando sem modelo --
def test_command_without_model(app):
    K = app.command
    r = K.run("quanto é 18% de 2450?")
    assert r["tool"] == "calcular" and r["proposal"]["value"] == 441 and "441" in r["reply"] and r["model"] is False
    assert "Sem o modelo" in (r["aviso"] or "") and "Sem o modelo" not in r["reply"]
    r = K.run("que horas são?")
    assert r["tool"] == "datas_horas" and r["proposal"]["action"] == "time" and "Agora são" in r["reply"]
    r = K.run("quantos dias faltam para 25/12?")
    assert r["proposal"]["days"] > 0 and r["proposal"]["target"].endswith("-12-25")

    r = K.run("me lembra de pagar a conta de luz amanhã às 15h")
    assert r["tool"] == "cards" and r["proposal"]["action"] == "create_card" and r.get("executed")
    card = r["results"][0]
    amanha = (dt.date.today() + dt.timedelta(1)).isoformat()
    assert card["kind"] == "lembrete" and card["start"] == f"{amanha}T15:00"
    assert card["title"] == "Pagar a conta de luz"
    assert app.cards.get(card["id"])
    assert K.confirm(r["id"], True)["already"]                   # confirmar de novo não duplica
    assert len(app.cards.list()) == 1

    r = K.run("reunião com o cliente dia 10/12 às 9h")
    c = r["results"][0]
    assert c["kind"] == "evento" and c["start"].endswith("-12-10T09:00") and c["end"].endswith("T10:00")

    r = K.run("adiciona leite, ovos e café na lista de compras")
    assert r["tool"] == "listas" and [x["text"] for x in r["results"][0]["checklist"]] == ["leite", "ovos", "café"]

    r = K.run("#a@b.com &oi&")
    assert r["tool"] == "bilhete" and r["kind"] == "mensagem" and r["needs_confirm"]
    assert r["proposal"]["to"] == "a@b.com" and r["proposal"]["body"] == "oi"
    assert r["authorization"]["decisao"] == "confirmar"
    with pytest.raises(Exception):
        K.confirm(r["id"], True)                              # sem integração conectada
    assert K.confirm(K.run("#a@b.com &oi&")["id"], False)["cancelled"]

    r = K.run("deixa o tema escuro")
    assert r["proposal"] == {"action": "ui", "patch": {"theme": "escuro"}, "descricao": "tema escuro"}
    assert app.ui.get()["theme"] == "escuro"

    r = K.run("me conta uma piada")
    assert r["tool"] == "nenhuma" and r["proposal"]["action"] == "none"

    from djorge_local.command import ModeloAusente
    with pytest.raises(ModeloAusente):
        K.run("sempre que chegar e-mail da Ana, me avisa")

    r = K.run("o que tenho amanhã?")
    assert r["tool"] == "proximas_tarefas" and any(x["id"] == card["id"] for x in r["results"])


# ------------------------------------------------------------------ busca --
def test_search(app):
    app.cards.create({"title": "Pagar boleto da escola", "due": "2026-11-10", "body": "R$ 450,00 vence dia 10"})
    app.cards.create({"title": "Comprar presente", "body": "aniversário da Ana"})
    app.hist.add({"type": "event", "event": {"source": "teste", "title": "Escola", "text": "reunião de pais na escola"}})
    r = app.search.search("boleto escola")
    assert r["results"][0]["title"] == "Pagar boleto da escola" and r["results"][0]["score"] == 1.0
    assert any(h["campo"] == "valor" for h in r["results"][0]["highlights"])
    assert any(h["campo"] == "termo" for h in r["results"][0]["highlights"])
    assert {x["source"] for x in r["results"]} == {"cards", "history"}
    assert all(x["source"] == "cards" for x in app.search.search("escola", "cards")["results"])
    assert app.search.search("xyzw")["results"] == []
    with pytest.raises(ValueError):
        app.search.search("a", "tudo")
    r = app.command.run("procura o presente da Ana")
    assert r["tool"] == "buscar" and r["results"][0]["title"] == "Comprar presente"


# ------------------------------------------------------------------ rotas --
def test_routes(srv, app):
    assert srv("/api/cards", token=False)[0] == 401
    code, c = srv("/api/cards", {"title": "Teste", "due": "2026-11-20"})
    assert code == 200 and c["id"]
    assert srv(f"/api/cards/{c['id']}", {"priority": 3})[1]["priority"] == 3
    assert srv("/api/cards?priority=3")[1]["cards"][0]["id"] == c["id"]
    assert srv("/api/cards/move", {"id": c["id"], "column": "hoje", "order": 1})[1]["column"] == "hoje"
    assert len(srv("/api/agenda?from=2026-11-19&to=2026-11-21")[1]["cards"]) == 1
    assert srv("/api/cards", {"title": "x", "kind": "bla"})[0] == 400
    assert srv(f"/api/cards/{c['id']}/delete", {})[1]["ok"]
    assert srv("/api/cards", {"title": "x"}, headers={"Origin": "http://evil.com"})[0] == 403

    code, r = srv("/api/command", {"text": "2+2*3"})
    assert code == 200 and r["proposal"]["value"] == 8
    code, r = srv("/api/command", {"text": "deixa o tema claro"})
    assert r["proposal"]["action"] == "ui" and srv("/api/ui")[1]["prefs"]["theme"] == "claro"
    code, r = srv("/api/command", {"text": "#ana@x.com &chego às 10h&"})
    assert r["needs_confirm"] and srv("/api/command/confirm", {"id": r["id"], "approve": False})[1]["cancelled"]
    assert srv("/api/command", {"text": "sempre que chegar e-mail do banco, me avisa"}) == (409, {"error": "modelo_ausente", "tool": "regra"})
    assert srv("/api/rules/preview", {"text": "mova e-mails da Ana para Trabalho"})[0] == 409
    assert srv("/api/ui", {"density": "compacta"})[1]["prefs"]["density"] == "compacta"
    assert srv("/api/ui", {"density": "gigante"})[0] == 400
    t = srv("/api/triage", {"text": "A fatura de R$ 230,00 vence amanhã", "remetente": "cobranca@enel.com.br"})[1]
    assert t["categoria"] == "financeiro" and t["model"] is False
    assert srv("/api/search?q=fatura")[0] == 200

    # módulos dos outros agentes: respondem 200 ou 503, nunca derrubam o app
    for p in ("/api/model/info", "/api/integrations", "/api/voice/status", "/api/trees/graph"):
        assert srv(p)[0] in (200, 503), p
    assert srv("/api/integrations/xyz/sync", {})[0] in (200, 400, 404, 500, 503)
    code, body = srv("/oauth/callback?state=nada&code=1", token=False)
    assert code in (200, 503)
    assert srv("/static/app/../../app.py", token=False)[0] == 404
    assert srv("/static/app/recorder.js", token=False)[0] in (200, 503)
    app._svc.pop("voice", None)
    assert srv("/api/voice/status")[0] == 503
