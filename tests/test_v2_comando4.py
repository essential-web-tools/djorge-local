"""Rodada 4, agente C: B3, B4, B6, P7, P9, lote do modelo e alertas pelo comando."""
from types import SimpleNamespace

import pytest

from tests.test_v2_core import app  # noqa: F401  (fixture)
from djorge_local import pistas as P
from djorge_local import formatar as F


def test_b3_pergunta_no_subjuntivo():
    q = P.pergunta(["cards", "bilhete"])
    falas = [o["fala"] for o in q["opcoes"]]
    assert falas == ["crie um card", "mande uma mensagem"]
    assert q["opcoes"][0]["rotulo"] == "Criar um card"


def test_b4_aviso_separado_e_fora_do_historico(app):
    r = app.command.run("quanto é 2+2?")
    assert r["aviso"] and "Sem o modelo" not in r["reply"]
    assert all("Sem o modelo" not in str(h) for h in app.hist.last(20))


@pytest.mark.parametrize("pedido", ["anota: comprar pão", "anote comprar pão", "me lembra de comprar pão",
                                    "cria uma tarefa: comprar pão", "anota aí: comprar pão"])
def test_b6_sem_verbo_no_titulo(app, pedido):
    r = app.command.run(pedido)
    assert r["proposal"]["card"]["title"] == "Comprar pão", r["proposal"]


def test_p7_nomes_capitalizados():
    assert F.titulo("reunião com a joana no escritório") == "Reunião com a Joana no escritório"
    assert F.titulo("reunião com a equipe") == "Reunião com a equipe"
    assert F.titulo("visita na padaria", ["padaria"]) == "Visita na Padaria"


def test_p9_limpar_historico(app):
    app.hist.add({"type": "command", "text": "x", "tool": "cards"})
    r = app.command.run("apaga o histórico")
    assert r["proposal"]["action"] == "history_clear" and r["proposal"]["tudo"] and r["needs_confirm"]
    res = app.command.confirm(r["id"], True)
    assert res["ok"] and res["apagados"] >= 1
    r = app.command.run("limpa o histórico de hoje")
    assert r["proposal"].get("hoje") and r["needs_confirm"]
    assert app.command.confirm(r["id"], True)["ok"]


class _Eng:
    def __init__(self):
        self.calls = []

    def run(self, reqs):
        self.calls.append(reqs)
        out = []
        for q in reqs:
            if q.kind == "NOUL":
                out.append({"p_true": 0.9})
            elif q.kind == "CHOICE":
                out.append({"distribution": {o: (0.9 if i == 3 else 0.01) for i, o in enumerate(q.options)}})
            elif q.target == "evento ou compromisso":
                i = q.source.find("reunião de orçamento")
                out.append(SimpleNamespace(matches=[SimpleNamespace(start=i, end=i + 20, probability=0.9,
                                                                    attributes={})] if i >= 0 else []))
            else:
                out.append(SimpleNamespace(matches=[]))
        return out


def test_modelo_um_lote_e_titulo_do_resolve(app):
    e = _Eng()
    app.command.engine = lambda: e
    r = app.command.run("por favor marca a reunião de orçamento amanhã às 10h")
    assert len(e.calls) == 1 and len(e.calls[0]) > 3          # todas as perguntas num só engine.run
    assert r["proposal"]["card"]["title"].casefold() == "reunião de orçamento"


@pytest.mark.parametrize("pedido", ["me lembra todo dia às 9h até sexta de tomar o remédio",
                                    "me avisa de 2 em 2 horas entre 8h e 18h"])
def test_alertas_viram_lembrete(app, pedido):
    r = app.command.run(pedido)
    assert r["tool"] == "cards" and r["proposal"]["action"] == "create_card", r["reply"]
    assert r["proposal"]["card"]["kind"] == "lembrete" and r["proposal"]["card"]["alertas"]
    assert not r["perguntas"]


def test_alerta_antes_no_card_em_foco(app):
    c = app.command.run("reunião amanhã às 15h")["results"][0]
    r = app.command.run("avisa 30 min antes", context={"selected_card": c["id"]})
    assert r["proposal"]["action"] == "update_card" and r["proposal"]["id"] == c["id"]
    assert r["proposal"]["card"]["alertas"][0]["antes"] == 30
