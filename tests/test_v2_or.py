"""Agente OR: prioridade dinâmica, informações faltantes, feriados/datas, floreios, contexto do usuário,
busca ampliada e configurações."""
import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_v2_secretario import app, srv  # noqa: E402,F401

from djorge_local import prioridade as pr  # noqa: E402

AGORA = dt.datetime(2026, 11, 5, 10, 0)


def _c(**k):
    base = {"id": k.pop("id", "a"), "title": "Tarefa", "kind": "tarefa", "priority": 0, "done": False, "column": "entrada",
            "meta": {}, "people": []}
    base.update(k)
    return base


# ------------------------------------------------------------------ 1) prioridade dinâmica --
def test_prioridade_sobe_perto_da_data():
    assert pr.calcular(_c(due="2026-11-20T10:00:00"), AGORA)["nivel"] == 0
    r = pr.calcular(_c(due="2026-11-07T10:00:00"), AGORA)
    assert r["nivel"] == 1 and "vence em 2 dias" in r["motivo"]
    r = pr.calcular(_c(due="2026-11-06T10:00:00"), AGORA)
    assert r["nivel"] == 2 and r["motivo"] == "sobe: vence amanhã"
    assert pr.calcular(_c(due="2026-11-05T18:00:00"), AGORA)["nivel"] == 3
    assert "atrasada" in pr.calcular(_c(due="2026-11-01T10:00:00"), AGORA)["motivo"]
    assert "começa amanhã" in pr.calcular(_c(start="2026-11-06T09:00:00", kind="evento"), AGORA)["motivo"]
    assert pr.calcular(_c(due="2026-11-06", done=True), AGORA)["nivel"] == 0


def test_carga_do_dia_conflito_e_ordem():
    dia = [_c(id="x", priority=3, due="2026-11-06T09:00:00"), _c(id="y", priority=2, due="2026-11-06T14:00:00")]
    r = pr.calcular(_c(id="a", due="2026-11-06T11:00:00"), AGORA, dia + [_c(id="a", due="2026-11-06T11:00:00")])
    assert r["conflito"] and r["nivel"] == 3
    assert r["motivo"] == "sobe: vence amanhã · 3 tarefas importantes no dia"
    assert r["ordem"][0] == "x" and set(r["ordem"]) == {"a", "x", "y"}


def test_origem_manual_nunca_e_sobrescrita(app):
    m = app.cards.create({"title": "Minha", "priority": 1, "due": (dt.date.today() + dt.timedelta(days=1)).isoformat()})
    assert m["meta"]["prioridade_origem"] == "manual"
    a = app.cards.create({"title": "Auto", "due": (dt.date.today() + dt.timedelta(days=1)).isoformat()})
    assert pr.origem(a) == "auto"
    assert pr.recalcular(app.cards) >= 1
    assert app.cards.get(m["id"])["priority"] == 1
    assert app.cards.get(a["id"])["priority"] == 2
    assert "amanhã" in app.cards.get(a["id"])["meta"]["prioridade_motivo"]
    # editar outro campo sem mexer na prioridade mantém auto; mudar a prioridade vira manual
    app.cards.update(a["id"], {"title": "Auto 2", "priority": 2})
    assert pr.origem(app.cards.get(a["id"])) == "auto"
    app.cards.update(a["id"], {"priority": 0})
    assert pr.origem(app.cards.get(a["id"])) == "manual"


def test_rotas_prioridade_e_motivo(srv, app):
    amanha = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    st, c = srv("/api/cards", {"title": "Relatório", "due": amanha + "T10:00:00"})
    assert st == 200 and c["prioridade_auto"]["motivo"].startswith("sobe: vence amanhã")
    st, r = srv("/api/cards")
    x = next(k for k in r["cards"] if k["id"] == c["id"])
    assert x["priority"] == 2 and x["prioridade_auto"]["origem"] == "auto"
    srv(f"/api/cards/{c['id']}", {"priority": 3})
    st, x = srv(f"/api/cards/{c['id']}/prioridade_auto", {})
    assert x["prioridade_auto"]["origem"] == "auto" and x["priority"] == 2


# ------------------------------------------------------------------ 2) informações faltantes --
def test_faltantes_e_respostas():
    c = _c(title="Pagar boleto da escola", meta={"origem": "notificacao"})
    f = [x["campo"] for x in pr.faltantes(c)]
    assert f == ["prazo", "valor"]
    assert pr.faltantes(_c(title="Pagar boleto"))  == []                 # criado à mão: não pergunta
    assert [x["campo"] for x in pr.faltantes(c, ["notificacao:valor"])] == ["prazo", "responsavel"]
    assert "valor" not in [x["campo"] for x in pr.faltantes(_c(title="Pagar R$ 120,00", meta={"origem": "comando"}))]
    ev = _c(kind="evento", title="Reunião com a Ana", start="2026-11-06T00:00:00", meta={"origem": "comando"})
    assert [x["campo"] for x in pr.faltantes(ev)] == ["horario", "local"]
    p = pr.responder(ev, "horario", "15:30")
    assert p["start"] == "2026-11-06T15:30:00"
    p = pr.responder(c, "valor", "R$ 300")
    assert p["body"].endswith("Valor: R$ 300") and p["meta"]["respostas"]["valor"] == "R$ 300"
    assert pr.responder(c, "prazo", "2026-11-10")["due"] == "2026-11-10T00:00:00"
    with pytest.raises(ValueError):
        pr.responder(c, "prazo", "amanhã")
    assert pr.recusar(c, "valor")["meta"]["nao_perguntar"] == ["valor"]


def test_rotas_perguntas(srv, app):
    c = app.cards.create({"title": "Pagar conta de luz", "meta": {"origem": "comando"}})
    st, x = srv(f"/api/cards/{c['id']}")
    assert [f["campo"] for f in x["faltando"]] == ["prazo", "valor"]
    st, x = srv(f"/api/cards/{c['id']}/responder", {"campo": "valor", "valor": "R$ 210"})
    assert st == 200 and [f["campo"] for f in x["faltando"]] == ["prazo"]
    st, x = srv(f"/api/cards/{c['id']}/nao_perguntar", {"campo": "prazo"})
    assert x["faltando"] == [] and app.cards.get(c["id"])["meta"]["nao_perguntar"] == ["prazo"]
    d = app.cards.create({"title": "Pagar aluguel", "meta": {"origem": "comando"}})
    st, x = srv(f"/api/cards/{d['id']}/nao_perguntar", {"campo": "valor", "escopo": "tipo"})
    assert "comando:valor" in app.ui.get()["nao_perguntar"]
    e = app.cards.create({"title": "Pagar internet", "meta": {"origem": "comando"}})
    assert "valor" not in [f["campo"] for f in srv(f"/api/cards/{e['id']}")[1]["faltando"]]
    st, i = srv("/api/inicio")
    assert any(p["card_id"] == e["id"] and p["campo"] == "prazo" for p in i["perguntas"])


def test_comando_marca_origem_e_ressalva(app):
    r = app.command.run("pagar o boleto da escola dia 15/11/2026")
    c = app.cards.get(r["results"][0]["id"]) if r.get("results") and r["results"][0].get("id") else None
    if c is None:
        c = next(x for x in app.cards.all() if "boleto" in x["title"].lower())
    assert c["meta"].get("origem") == "comando"
    assert "feriado" in r["reply"].lower() or "bancos fechados" in r["reply"].lower()
    assert r.get("conflitos") and r["conflitos"][0]["sugestao"] == "2026-11-13"


# ------------------------------------------------------------------ 3) datas --
def test_datas_alerta_e_conflito(srv, app):
    app.ui.set({"uf": "SP"})
    st, d = srv("/api/datas?dias=60")
    assert st == 200 and d["uf"] == "SP"
    assert any(x["nome"].startswith("Proclamação") for x in d["datas"])
    alvo = d["datas"][0]
    st, r = srv("/api/datas/alerta", {"chave": alvo["chave"], "ativo": not alvo["alerta"]})
    assert r["ativo"] == (not alvo["alerta"])
    st, d2 = srv("/api/datas?dias=60")
    assert next(x for x in d2["datas"] if x["chave"] == alvo["chave"])["alerta"] == (not alvo["alerta"])
    st, c = srv("/api/cards", {"title": "Pagar boleto", "due": "2026-11-15T10:00:00"})
    assert c["conflitos"] and c["conflitos"][0]["sugestao"] == "2026-11-13"
    st, c2 = srv(f"/api/cards/{c['id']}/mover_dia", {"data": "2026-11-13"})
    assert c2["due"].startswith("2026-11-13") and not c2["conflitos"]


def test_aniversario_e_aviso_de_data(app):
    hoje = dt.date.today()
    app.cards.create({"title": "Aniversário da Ana", "kind": "evento", "start": hoje.isoformat() + "T00:00:00",
                      "all_day": True, "recurrence": "anual"})
    from djorge_local.app import avisar_datas, proximas_datas
    assert any(a["nome"].lower().startswith("ana") for a in proximas_datas(app, 5)["aniversarios"])
    from djorge_local import alertas as al
    s = al.servico(app)
    s.mostrar = lambda *a: True
    n = avisar_datas(app)
    assert n >= 1
    assert any("Ana" in a["titulo"] for a in s.avisos(20))
    assert avisar_datas(app) == 0                                    # uma vez por dia


# ------------------------------------------------------------------ 4) floreios --
def test_floreios_no_disparo(app):
    from djorge_local import alertas as al
    s = al.servico(app)
    vistos = []
    s.mostrar = lambda t, m: vistos.append((t, m)) or True
    a = s.criar({"quando": (dt.datetime.now() - dt.timedelta(minutes=1)).isoformat(timespec="minutes")},
                titulo="Boleto vence hoje")
    ev = s._disparar(s.get(a["id"]), dt.datetime.now())
    assert ev["tom"] == "serio" and ev["titulo"] != "Boleto vence hoje"
    app.ui.set({"floreios": False})
    ev = s._disparar(s.get(a["id"]), dt.datetime.now())
    assert ev["titulo"] == "Boleto vence hoje"


# ------------------------------------------------------------------ 5) contexto do usuário --
def test_contexto_sobre_voce(srv, app):
    st, r = srv("/api/ui", {"sobre": "Moro em São Paulo,   trabalho em banco e tenho dois filhos. senha: 1234"})
    p = app.ui.get()
    assert "[OCULTO]" in p["sobre"] and "  " not in p["sobre"]
    st, u = srv("/api/ui")
    assert u["contexto"]["texto"] == "SP · banco · filhos"
    assert "filhos" in u["contexto"]["texto"]
    with pytest.raises(ValueError):
        app.ui.set({"uf": "XX"})
    from djorge_local.command import com_contexto

    class Eng:
        def run(self, reqs):
            self.vistos = reqs
            return []
    e = Eng()
    from djorge.runtime.semantic import SemReq
    com_contexto(e, app).run([SemReq("CHOICE", "oi", options=["a"]), SemReq("resolve", "oi", mode="ENTITY", target="x")])
    assert e.vistos[0].source.startswith("Sobre o usuário:") and e.vistos[1].source == "oi"


# ------------------------------------------------------------------ 6) busca ampliada --
def test_busca_notas_notificacoes(srv, app):
    app.notas.criar({"titulo": "Receita de bolo", "corpo": "farinha e ovos"})
    st, r = srv("/api/search?q=bolo")
    assert any(x.get("scope") == "notas" and x["title"] == "Receita de bolo" for x in r["results"])
