"""Alertas (V2_SPEC_4): formato, cálculo do próximo disparo, agendador, rotas e comandos em texto."""
import datetime as dt
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from djorge_local import alertas as A

QUI = dt.datetime(2026, 11, 5, 10, 0)          # quinta-feira


class Relogio:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture()
def svc(tmp_path):
    toasts = []
    r = Relogio(QUI)
    s = A.Alertas(tmp_path / "alertas.db", agora=r, mostrar=lambda t, m="": toasts.append((t, m)))
    s.stop()                                     # nos testes o relógio anda à mão via tick()
    s.start = lambda: None
    yield s, r, toasts
    s.close()


def test_normalizar_formato():
    a = A.normalizar({"quando": "2026-11-06 09:00", "repetir": "todo dia", "ate": "2026-11-10",
                      "janela": "08:00-20:00", "canal": "toast"})
    assert a["quando"] == "2026-11-06T09:00" and a["repetir"] == {"freq": "diaria"}
    assert a["ate"] == "2026-11-10T23:59" and a["janela"] == {"inicio": "08:00", "fim": "20:00"}
    assert a["canal"] == ["toast"]
    assert A.normalizar({"antes": "30 min"})["antes"] == 30
    assert A.normalizar({"antes": "1 hora"})["antes"] == 60
    with pytest.raises(ValueError):
        A.normalizar({"canal": ["toast"]})
    with pytest.raises(ValueError):
        A.normalizar({"quando": "2026-11-06", "canal": ["pombo"]})
    assert A.normalizar_lista(None) == [] and len(A.normalizar_lista({"quando": "2026-11-06T10:00"})) == 1


def test_de_texto_todo_dia_ate_sexta():
    d = A.de_texto("me lembra todo dia às 9h até sexta de tomar o remédio", QUI)
    a = d["alertas"][0]
    assert a["repetir"]["freq"] == "diaria" and a["repetir"]["hora"] == "09:00"
    assert a["quando"] == "2026-11-06T09:00" and a["ate"] == "2026-11-06T23:59"
    assert d["titulo"] == "Tomar o remédio" and d["card"]["kind"] == "lembrete"


def test_de_texto_de_2_em_2_horas_na_janela():
    d = A.de_texto("me avisa de 2 em 2 horas entre 8h e 18h", QUI)
    a = d["alertas"][0]
    assert a["repetir"] == {"freq": "horaria", "intervalo": 2}
    assert a["janela"] == {"inicio": "08:00", "fim": "18:00"} and a["quando"] == "2026-11-05T12:00"


def test_de_texto_antes_e_semanal_e_nao_alerta():
    d = A.de_texto("avisa 30 min antes", QUI)
    assert d["alertas"][0]["antes"] == 30 and "card" not in d
    d = A.de_texto("toda segunda às 8h", QUI)
    a = d["alertas"][0]
    assert a["repetir"] == {"freq": "semanal", "dias_semana": [0], "hora": "08:00"}
    assert a["quando"] == "2026-11-09T08:00"
    assert A.de_texto("qual a capital da França?", QUI) is None
    assert A.de_texto("me lembra amanhã às 15h de ligar pro João", QUI)["alertas"][0]["quando"] == "2026-11-06T15:00"


def test_disparo_unico_toast_e_aviso(svc):
    s, r, toasts = svc
    a = s.criar({"quando": "2026-11-05T10:30", "titulo": "Reunião"})
    assert a["proximo"] == "2026-11-05T10:30" and a["ativo"]
    assert s.tick(dt.datetime(2026, 11, 5, 10, 29)) == []
    ev = s.tick(dt.datetime(2026, 11, 5, 10, 30))
    assert ev[0]["canais"] == ["toast", "app"] and toasts == [("Reunião", "")]
    assert s.get(a["id"])["estado"] == "concluido" and not s.get(a["id"])["ativo"]
    av = s.avisos()
    assert av[0]["titulo"] == "Reunião" and not av[0]["lido"]
    assert s.tick(dt.datetime(2026, 11, 5, 12, 0)) == []


def test_repetir_diario_ate_e_concluir(svc):
    s, r, toasts = svc
    d = A.de_texto("me lembra todo dia às 9h até domingo de tomar o remédio", QUI)
    a = s.criar(d["alertas"][0], titulo=d["titulo"])
    assert a["proximo"] == "2026-11-06T09:00"
    s.tick(dt.datetime(2026, 11, 6, 9, 0))
    assert s.get(a["id"])["proximo"] == "2026-11-07T09:00"
    r.t = dt.datetime(2026, 11, 7, 9, 5)
    s.tick()
    assert s.get(a["id"])["proximo"] == "2026-11-08T09:00"
    s.tick(dt.datetime(2026, 11, 8, 9, 0))
    assert not s.get(a["id"])["ativo"] and s.get(a["id"])["estado"] == "concluido"   # passou do "até"
    assert len(toasts) == 3


def test_janela_de_horas(svc):
    s, r, toasts = svc
    a = s.criar(A.de_texto("me avisa de 2 em 2 horas entre 8h e 18h de beber água", QUI)["alertas"][0])
    vistos = []
    t = QUI
    for _ in range(8):
        nx = dt.datetime.fromisoformat(s.get(a["id"])["proximo"])
        vistos.append(nx.strftime("%d %H:%M"))
        s.tick(nx)
    assert vistos == ["05 12:00", "05 14:00", "05 16:00", "05 18:00", "06 08:00", "06 10:00", "06 12:00", "06 14:00"]


def test_antes_do_card_adiar_desativar(svc):
    s, r, toasts = svc
    card = {"id": "c1", "title": "Dentista", "start": "2026-11-05T15:00", "alertas": [{"id": "x1", "antes": 30}]}
    out = s.sync_card(card)
    assert out[0]["proximo"] == "2026-11-05T14:30" and out[0]["card_id"] == "c1"
    r.t = dt.datetime(2026, 11, 5, 14, 30)
    s.tick()
    a = s.adiar("x1", 15)
    assert a["proximo"] == "2026-11-05T14:45" and a["estado"] == "adiado" and a["ativo"]
    s.tick(dt.datetime(2026, 11, 5, 14, 45))
    assert len(toasts) == 2 and not s.get("x1")["ativo"]
    s.ativar("x1")
    assert s.desativar("x1")["estado"] == "desativado"
    card["alertas"] = []
    s.sync_card(card)
    assert s.get("x1") is None
    with pytest.raises(ValueError):
        s.criar({"antes": 10})                   # "antes" sem card com data


def test_concluir_repetido_pula_para_proxima(svc):
    s, r, toasts = svc
    a = s.criar({"repetir": {"freq": "semanal", "dias_semana": [0], "hora": "08:00"}, "titulo": "Feira"})
    assert a["proximo"] == "2026-11-09T08:00"
    assert s.concluir(a["id"])["proximo"] == "2026-11-16T08:00"
    b = s.criar({"quando": "2026-11-05T11:00"})
    assert s.concluir(b["id"])["estado"] == "concluido"


def test_agendador_so_roda_com_alerta_ativo(tmp_path):
    import time
    toasts = []
    r = Relogio(QUI)
    s = A.Alertas(tmp_path / "a.db", agora=r, mostrar=lambda t, m="": toasts.append(t))
    s.start()
    assert not s.rodando()
    s.criar({"quando": "2026-11-05T09:00", "titulo": "Atrasado"})   # já venceu: dispara logo
    for _ in range(50):
        if toasts:
            break
        time.sleep(0.05)
    assert toasts == ["Atrasado"]
    for _ in range(50):
        if not s.rodando():
            break
        time.sleep(0.05)
    assert not s.rodando()
    s.close()


def test_rotas(tmp_path, monkeypatch):
    s = A.Alertas(tmp_path / "r.db", agora=Relogio(QUI), mostrar=lambda *a: None)
    card = {"id": "c9", "title": "Consulta", "start": "2026-11-06T10:00"}
    app = SimpleNamespace(_alertas=s, cards=SimpleNamespace(get=lambda i: card if i == "c9" else None))
    assert A.rotas(app, "GET", "/api/cards", {}) is None
    st, d = A.rotas(app, "POST", "/api/alertas", {"quando": "2026-11-05T12:00", "titulo": "Almoço"})
    assert st == 200 and d["alerta"]["titulo"] == "Almoço"
    aid = d["alerta"]["id"]
    st, d = A.rotas(app, "POST", "/api/alertas", {"texto": "avisa 30 min antes", "card_id": "c9"})
    assert st == 200 and d["alertas"][0]["proximo"] == "2026-11-06T09:30"
    st, d = A.rotas(app, "GET", "/api/alertas?x=1", None)
    assert st == 200 and len(d["alertas"]) == 2 and d["adiar"] == [5, 15, 60]
    assert A.rotas(app, "POST", f"/api/alertas/{aid}/adiar", {"min": 5})[1]["alerta"]["proximo"] == "2026-11-05T10:05"
    assert A.rotas(app, "POST", f"/api/alertas/{aid}/concluir", {})[1]["alerta"]["estado"] == "concluido"
    assert A.rotas(app, "POST", f"/api/alertas/{aid}/desativar", {})[1]["alerta"]["estado"] == "desativado"
    assert A.rotas(app, "POST", "/api/alertas/nada/concluir", {})[0] == 404
    assert A.rotas(app, "POST", "/api/alertas", {"quando": "blablabla xyz"})[0] == 400
    s.tick(dt.datetime(2026, 11, 6, 9, 30))
    st, d = A.rotas(app, "GET", "/api/alertas", None)
    assert d["nao_lidos"] == 1
    assert A.rotas(app, "POST", "/api/alertas/avisos/lidos", {})[1]["n"] == 1
    s.close()


def test_card_guarda_alertas(tmp_path):
    from djorge_local.cards import Cards
    c = Cards(tmp_path / "cards.db")
    k = c.create({"title": "Remédio", "kind": "lembrete", "alertas": {"quando": "2026-11-06T09:00", "repetir": "todo dia"}})
    assert k["alertas"][0]["repetir"] == {"freq": "diaria"} and k["alertas"][0]["id"]
    assert c.get(k["id"])["alertas"] == k["alertas"]
    assert c.create({"title": "sem"})["alertas"] == []
    with pytest.raises(ValueError):
        c.create({"title": "x", "alertas": [{"canal": ["toast"]}]})
    c.close()
