"""Rodada X: pedidos condicionais viram automação (nunca bilhete), ação "destacar", destaques da interface
(uiprefs), notificações sem repetição, laço do toast do próprio Djorge, deduplicar cards, intenção da triagem e
objetivo/descrição do card."""
import datetime as dt
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_v2_secretario import _ev, app, srv  # noqa: E402,F401

TAMY = ("Crie um alerta dizendo \"Seu amor mandou mensagem\" sempre que a Tamy Pqna enviar mensagem e coloque a "
        "mensagem dela em destaque na interface")


# ------------------------------------------------------------------ A) condicional = regra --
@pytest.mark.parametrize("t", [TAMY, "Crie um alerta dizendo Seu amor mandou mensagem quando a Tamy mandar mensagem",
                               "quando o João mandar e-mail, me avisa", "toda vez que chegar mensagem do banco cria uma tarefa",
                               "se a Ana me escrever, me avisa", "caso chegue e-mail do chefe, destaca"])
def test_condicional_e_regra(t):
    from djorge_local import pistas as P
    p = P.pistas(t)
    assert P.condicional(t) and p["condicional"]
    d = P.decidir(None, p, texto=t)
    assert d["tool"] == "regra"
    # mesmo com o modelo apontando bilhete, o guarda-corpo segura
    assert P.decidir_modelo({"bilhete": 0.9, "regra": 0.05}, p, texto=t)["tool"] == "regra"


@pytest.mark.parametrize("t", ["manda uma mensagem pra Tamy dizendo oi", "manda pra Ana: se chegar atrasado me liga",
                               "avisa a Ana que quando ela chegar é pra me ligar"])
def test_bilhete_continua_bilhete(t):
    from djorge_local import pistas as P
    assert not P.condicional(t) and P.decidir(None, P.pistas(t), texto=t)["tool"] == "bilhete"


def test_de_texto_tamy():
    from djorge_local.automacoes import compilar, de_texto
    r = de_texto(TAMY)
    a = r["automacao"]
    assert a["quando"]["remetente"] == "Tamy Pqna"
    acoes = [e["acao"] for e in a["entao"]]
    assert acoes == ["notificar", "destacar"]
    assert a["entao"][0]["params"]["titulo"] == "Seu amor mandou mensagem"
    assert a["entao"][1]["params"]["cor"] == "rosa" and a["entao"][1]["params"]["fixar"] is True
    assert r["resumo_curto"] == ("Se a Tamy Pqna mandar mensagem → aviso “Seu amor mandou mensagem” e a mensagem dela "
                                 "fica em destaque")
    t = compilar(a)
    assert t["nodes"]["filtro"]["if"]["sender_has"] == ["Tamy Pqna"]
    sem = de_texto("Crie um alerta dizendo Seu amor mandou mensagem sempre que a Tamy Pqna enviar mensagem")
    assert sem["automacao"]["entao"][0]["params"]["titulo"] == "Seu amor mandou mensagem"
    j = de_texto("quando o João mandar e-mail, me avisa")["automacao"]
    assert j["quando"]["remetente"] == "João" and j["quando"]["fontes"] == ["email"] and j["entao"][0]["acao"] == "notificar"
    b = de_texto("toda vez que chegar mensagem do banco cria uma tarefa")["automacao"]
    assert b["quando"]["categoria"] == "financeiro" and b["entao"][0]["acao"] == "tarefa"
    n = de_texto("se a Ana me escrever, me avisa")["automacao"]
    assert n["quando"]["remetente"] == "Ana" and n["entao"][0]["acao"] == "notificar"


def test_comando_propoe_confirma_e_salva(app):
    K = app.command
    r = K.run(TAMY)
    assert r["tool"] == "regra" and r["kind"] != "mensagem"
    p = r["proposal"]
    assert p["action"] == "create_automacao" and r["needs_confirm"]
    assert "Se a Tamy Pqna mandar mensagem → aviso “Seu amor mandou mensagem”" in r["reply"]
    res = K.confirm(r["id"], True)
    assert res["ok"] and res["automacao"]["quando"]["remetente"] == "Tamy Pqna"
    assert any(a["nome"] == res["automacao"]["nome"] for a in app.secretario.automacoes.listar())
    assert K.run("manda uma mensagem pra Tamy dizendo oi")["tool"] == "bilhete"


def test_automacao_destaca_e_avisa(app):
    from djorge_local.automacoes import de_texto
    sec = app.secretario
    sec.configurar({"ativo": True, "modo": "sugerir"})
    sec.automacoes.salvar(de_texto(TAMY)["automacao"])
    out = app.handle(_ev("oi amor, chego às 8", title="Tamy Pqna"))["secretario"]
    n = sec.store.get(out["notificacao"])
    assert n["destaque"] and n["destaque"]["cor"] == "rosa" and n["destaque"]["fixar"]   # destacar vale mesmo no sugerir
    assert any(s["action"] == "notificar" for s in n["sugestoes"])
    assert any(x["_tipo"] == "notificacao" and x["id"] == n["id"] for x in app.inicio()["destaques"])
    sec.desfazer(n["id"])
    assert sec.store.get(n["id"])["destaque"] is None


# ------------------------------------------------------------------ B) destaques da interface --
def test_uiprefs_destaques_validados(tmp_path):
    from djorge_local.uiprefs import UIPrefs
    u = UIPrefs(tmp_path / "ui.json")
    ok = u.set({"destaques": [{"tipo": "remetente", "alvo": "Tamy", "estilo": {"cor": "vinho", "icone": "coracao"}}],
                "inicio_secoes": ["mensagens", "destaques", "tarefas", "eventos", "notificacoes"],
                "secao_destaque": "mensagens"})
    assert ok["destaques"][0]["estilo"] == {"cor": "vinho", "icone": "coracao", "fixar": True}
    for ruim in ({"destaques": [{"tipo": "css", "alvo": "x"}]},
                 {"destaques": [{"tipo": "card", "alvo": "<script>"}]},
                 {"destaques": [{"tipo": "card", "alvo": "x", "estilo": {"cor": "#ff0000"}}]},
                 {"destaques": [{"tipo": "secao", "alvo": "rodape"}]},
                 {"inicio_secoes": ["tarefas", "tarefas"]}, {"secao_destaque": "x"}):
        with pytest.raises(ValueError):
            u.set(ruim)


def test_uiprefs_comandos_de_destaque(tmp_path):
    from djorge_local.uiprefs import UIPrefs
    u = UIPrefs(tmp_path / "ui.json")
    hoje = dt.date(2026, 11, 5)

    def go(t):
        r = u.parse(t, hoje)
        assert r, t
        u.set(r["patch"])
        return r
    go("deixa as mensagens da Tamy em destaque")
    assert u.get()["destaques"][0]["tipo"] == "remetente" and u.get()["destaques"][0]["alvo"] == "Tamy"
    go("destaca as tarefas urgentes no início")
    assert any(d["alvo"] == "@urgentes" for d in u.get()["destaques"])
    r = go("deixa as mensagens do João em destaque em azul até amanhã")
    d = [x for x in u.get()["destaques"] if x["alvo"] == "João"][0]
    assert d["estilo"]["cor"] == "azul" and d["ate"] == "2026-11-06"
    go("coloca a agenda em destaque")
    assert u.get()["secao_destaque"] == "eventos"
    go("esconde as notificações do início")
    assert u.get()["inicio_ocultas"] == ["notificacoes"]
    go("coloca as mensagens primeiro no início")
    assert u.get()["inicio_secoes"][0] == "mensagens"
    go("tira o destaque da Tamy")
    assert not any(x["alvo"] == "Tamy" for x in u.get()["destaques"])
    go("tira o destaque")
    assert u.get()["destaques"] == [] and u.get()["secao_destaque"] == ""
    assert u.parse("muda a cor de destaque para verde")["patch"] == {"accent": "verde"}
    assert u.parse(TAMY) is None


def test_comando_ui_destaque(app):
    r = app.command.run("deixa as mensagens da Tamy em destaque")
    assert r["proposal"]["action"] == "ui" and app.ui.get()["destaques"][0]["alvo"] == "Tamy"


def test_notificacao_destaque_rota_e_migracao(app, srv, tmp_path):
    st = app.secretario.store
    n = st.add(_ev("teste", title="Fulano"))
    code, r = srv(f"/api/notificacoes/{n['id']}", {"destaque": {"cor": "verde", "fixar": True}})
    assert code == 200 and r["destaque"]["cor"] == "verde"
    assert srv(f"/api/notificacoes/{n['id']}", {"destaque": {"cor": "#123"}})[0] == 400
    assert srv(f"/api/notificacoes/{n['id']}", {"destaque": None})[1]["destaque"] is None
    # banco antigo, sem as colunas novas e com repetidas
    p = tmp_path / "velho.db"
    db = sqlite3.connect(p)
    db.execute("""CREATE TABLE notif (id TEXT PRIMARY KEY, t TEXT, fonte TEXT, app TEXT, titulo TEXT, texto TEXT,
        remetente TEXT, etiqueta TEXT, prioridade TEXT, nota INTEGER, motivos TEXT, regras TEXT, acoes TEXT,
        sugestoes TEXT, card_id TEXT, lida INTEGER DEFAULT 0, arquivada INTEGER DEFAULT 0, dados TEXT)""")
    for i, t in enumerate(("2026-11-01T10:00:00", "2026-11-01T10:05:00", "2026-11-01T11:00:00", "2026-11-03T10:00:00")):
        db.execute("INSERT INTO notif (id, t, fonte, app, titulo, texto) VALUES (?,?,?,?,?,?)",
                   (f"n{i}", t, "notifications", "WhatsApp", "Ana", "Oi!"))
    db.commit(); db.close()
    from djorge_local.notificacoes import Notificacoes
    s2 = Notificacoes(p)
    lst = s2.list(limit=10)
    assert len(lst) == 2 and max(x["contagem"] for x in lst) == 3 and all(x["destaque"] is None for x in lst)


# ------------------------------------------------------------------ repetidas e o laço --
def test_notificacao_repetida_nao_roda_secretario_de_novo(app):
    sec = app.secretario
    sec.configurar({"ativo": True, "modo": "agir"})
    a = app.handle(_ev("Preciso do orçamento hoje", title="Marta"))["secretario"]
    cards = len(app.cards.all())
    b = app.handle(_ev("Preciso do orçamento hoje", title="Marta"))["secretario"]
    c = app.handle(dict(_ev("Preciso do orçamento hoje", title="Marta")))["secretario"]
    assert b["duplicada"] and c["duplicada"] and b["notificacao"] == a["notificacao"] and not b["acoes"]
    assert sec.store.get(a["notificacao"])["contagem"] == 3 and len(app.cards.all()) == cards
    assert len([n for n in sec.store.list(limit=50) if n["titulo"] == "Marta"]) == 1


def test_laco_do_toast_um_card_so(app, monkeypatch):
    from djorge_local import toasts
    from djorge_local.automacoes import de_texto
    from djorge_local.connectors import proprio_app
    assert proprio_app("Djorge Local") and proprio_app("pythonw.exe") and proprio_app("Djorge.Local.2")
    assert not proprio_app("WhatsApp")
    sec = app.secretario
    sec.configurar({"ativo": True, "modo": "agir"})
    sec.automacoes.salvar(de_texto("Se eu receber uma mensagem sobre orçamento, crie um card de alerta")["automacao"])
    capturados = []
    monkeypatch.setattr(toasts, "mostrar", lambda t, m: capturados.append((t, m)) or True)
    txt = "Não esquece de me mandar o orçamento para criação do site"
    app.handle(_ev(txt, title="Carlos"))
    for _ in range(5):                         # o toast volta como notificação do Windows (app do Djorge)
        for t, m in list(capturados):
            app.handle(_ev(m, title=t, app="Djorge Local"))
        app.handle(_ev(txt, title="Carlos", app="WhatsApp") | {"id": "outro"})
    iguais = [c for c in app.cards.all() if "orçamento" in (c.get("title") or "").casefold() or "orcamento" in
              (c.get("title") or "").casefold()]
    assert len(iguais) == 1
    assert not [n for n in sec.store.list(limit=100) if n["app"] == "Djorge Local"]


def test_card_igual_em_24h_nao_duplica(app):
    sec = app.secretario
    sec.configurar({"ativo": True, "modo": "agir"})
    ctx = {"event": {"title": "X", "text": "orçamento", "notif_id": None}, "results": {}, "mode": None}
    r1 = app.actions.executar_nova({"action": "criar_card", "titulo": "Pagar luz"}, ctx)
    r2 = app.actions.executar_nova({"action": "criar_card", "titulo": "pagar  LUZ"}, ctx)
    assert r1["card_id"] == r2["card_id"] and r2["status"] == "já existia"


def test_deduplicar_cards(app, srv):
    for _ in range(4):
        app.cards.create({"title": "Lembrete orçamento", "kind": "lembrete", "tags": ["orçamento"],
                          "source": {"integration": "secretario", "external_id": "n1"}})
    app.cards.create({"title": "Lembrete orçamento", "kind": "lembrete"})      # feito à mão: fica
    assert srv("/api/cards/deduplicar", {"simular": True})[1]["repetidos"] == 3
    assert srv("/api/cards/deduplicar", {})[1]["juntados"] == 3
    assert len([c for c in app.cards.all() if c["title"] == "Lembrete orçamento"]) == 2


# ------------------------------------------------------------------ intenção da triagem --
def test_intencao(app, srv):
    from djorge_local.secretario import intencao
    n = {"sugestoes": [{"i": 0, "action": "tarefa", "card": {"title": "Enviar relatório", "due": "2026-11-06T18:00"}},
                       {"i": 1, "action": "notificar"}, {"i": 2, "action": "etiquetar", "etiqueta": "trabalho"}]}
    t = intencao(n, "Djorge", hoje=dt.date(2026, 11, 2))
    assert t["quer"].startswith("O Djorge quer: criar a tarefa “Enviar relatório” até") and "avisar você" in t["quer"]
    assert "etiquetar Trabalho" in t["quer"] and "%" not in t["quer"] and len(t["itens"]) == 3
    f = intencao({"acoes": [{"action": "etiquetar", "etiqueta": "trabalho", "desfazer": {"tipo": "etiqueta"}}]})
    assert f["fez"] == "O Djorge fez: etiquetar Trabalho" and f["pode_desfazer"]
    code, r = srv("/api/triage", {"text": "Oi, pode me enviar o relatório até sexta? Obrigado", "remetente": "Carlos"})
    assert code == 200 and r["intencao"]["quer"].startswith("O Djorge quer:")
    app.secretario.store.add(_ev("oi", title="Y"))
    assert "intencao" in srv("/api/notificacoes")[1]["notificacoes"][0]


# ------------------------------------------------------------------ objetivo do card --
def test_objetivo_do_card(app):
    c = app.cards.create({"title": "Revisar contrato", "objetivo": "  Fechar   a venda ", "body": "ler tudo"})
    assert c["objetivo"] == "Fechar a venda" and app.cards.get(c["id"])["objetivo"] == "Fechar a venda"
    assert app.cards.create({"title": "Sem"})["objetivo"] == ""
    r = app.command.run("cria uma tarefa revisar contrato amanhã com objetivo de fechar a venda")
    card = r["results"][0]
    assert card["objetivo"] == "Fechar a venda" and "objetivo" not in card["title"].casefold()
    r = app.command.run("tarefa relatório mensal objetivo: mostrar resultados; descrição: incluir gráficos")
    card = r["results"][0]
    assert card["objetivo"] == "Mostrar resultados" and card["body"] == "Incluir gráficos"
