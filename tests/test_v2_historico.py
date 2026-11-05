"""Rodada 4: histórico = ações (não conversa), alterações de cards, migração do histórico antigo, erros amigáveis."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from test_v2_secretario import app, srv  # noqa: F401,E402  (mesmos fixtures)


def test_migra_e_apaga_historico_antigo(tmp_path):
    from djorge_local.historico import Historico
    old = tmp_path / "historico.jsonl"
    old.write_text("\n".join(json.dumps(r) for r in [
        {"type": "command", "text": "marca dentista dia 20", "tool": "card", "reply": "Marquei o dentista…",
         "action": "create_card", "t": "2026-10-01T10:00:00", "id": "a1"},
        {"type": "log", "message": "x", "t": "2026-10-02T10:00:00", "id": "a2"}]) + "\nlixo\n", encoding="utf-8")
    h = Historico({"history": {"keep": 100, "mask_pii": False}}, tmp_path)
    assert not old.exists()
    itens = h.last()
    assert [i["id"] for i in itens] == ["a2", "a1"]
    cmd = itens[1]
    assert cmd["pedido"] == "marca dentista dia 20" and cmd["ferramenta"] == "card" and cmd["acao"] == "create_card"
    assert "reply" not in cmd and "Marquei" not in h.file.read_text(encoding="utf-8")
    assert set(["id", "t", "pedido", "ferramenta", "acao", "alvo", "resultado", "alteracoes", "desfeito"]) <= set(cmd)


def test_antes_de_invalido_nao_apaga(app, srv):
    app.hist.add({"type": "log", "message": "m"})
    code, r = srv("/api/history/clear", {"antes_de": "xx"})
    assert code == 400 and "data" in r["error"] and len(app.hist.last()) == 1


def test_alteracoes_de_card_pela_ui(app, srv):
    code, c = srv("/api/cards", {"title": "Dentista", "kind": "tarefa"})
    srv(f"/api/cards/{c['id']}", {"title": "Dentista 9h", "priority": 2})
    code, r = srv(f"/api/cards/{c['id']}/alteracoes")
    assert code == 200
    campos = {(a["campo"], a["de"], a["para"]) for a in r["alteracoes"]}
    assert ("title", "Dentista", "Dentista 9h") in campos and ("criado", None, "Dentista") in campos
    acao = app.hist.last()[0]
    assert acao["ferramenta"] == "ui" and acao["acao"] == "editar_card" and acao["alvo"]["id"] == c["id"]
    assert any(a["campo"] == "title" and a["para"] == "Dentista 9h" for a in acao["alteracoes"])
    # mesmo id: atualiza, não duplica
    code, c2 = srv("/api/cards", {"id": c["id"], "title": "Dentista 10h"})
    assert c2["id"] == c["id"] and len(app.cards.all()) == 1


def test_comando_vira_acao_sem_resposta(app, srv):
    code, r = srv("/api/command", {"text": "marca dentista dia 20 às 9h"})
    assert code == 200
    a = next(x for x in app.hist.last() if x.get("id") == r["id"])
    assert a["pedido"].startswith("marca dentista") and "reply" not in a
    if r.get("executed"):
        assert a["alvo"]["tipo"] == "card" and a["alteracoes"]
    code, r = srv("/api/command", {"text": "x" * 2500})
    assert code == 400 and "2000" in r["error"]


def test_erros_amigaveis(app, srv):
    code, r = srv("/api/cards/naoexiste", {"title": "x"})
    assert code == 404 and not r["error"].startswith("KeyError")
    code, r = srv("/api/event", {})
    assert code == 400
