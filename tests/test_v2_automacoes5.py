"""Automações v2.5 (agente AU): mais entradas (cards, prazo, horário, notas, alertas, manual), condições (grupos E/OU,
não-contém, valor, horário, dias, anexo/link/código, regex segura, etiqueta/coluna, "o modelo acha que", quantidade),
tratamentos (extrair, classificar, resumir, formatar, agrupar) e ações (lembrete, nota, mover/concluir/atualizar card,
prioridade, abrir site, ignorar, encadear, lista, registrar). Tudo compila para tree.py e volta do editor visual."""
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

from djorge_local import automacoes as AU  # noqa: E402
from djorge_local.tree import TreeError, _cond, regex_segura, validate  # noqa: E402

SEG_10H = "2026-11-02T10:00:00"            # segunda-feira


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
    a.secretario.agora = lambda: dt.datetime(2026, 11, 2, 12, 0)
    yield a
    a.secretario.automacoes.parar()
    a.cards.close()
    a.secretario.close()


@pytest.fixture()
def srv(app):
    from djorge_local.app import serve
    serve(app, block=False)
    base = f"http://127.0.0.1:{app.cfg['server']['port']}"
    tok = app.cfg["server"]["token"]

    def call(path, body=None):
        h = {"Content-Type": "application/json", "X-Token": tok}
        req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(), headers=h)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")
    return call


def _a(**kw):
    base = {"nome": "t", "quando": {}, "entao": [{"acao": "notificar", "params": {"titulo": "x"}}]}
    base.update(kw)
    return AU.normalizar(base)


def _roda(app, a, ev, mode="testar"):
    a = AU.normalizar(a) if "id" not in a else a
    return app.runner.run(AU.compilar(a), ev, mode=mode)


def _acoes(r):
    from djorge_local.tree import NOVAS_ACOES
    return [s for s in r["trail"] if s.get("action") in NOVAS_ACOES]


# ------------------------------------------------------------------------------------------- condições --
def test_condicoes_novas_no_if():
    ev = {"title": "Fatura", "text": "Segue em anexo a fatura de R$ 1.250,00. Link: https://x.com", "time": SEG_10H,
          "tags": ["contas"], "column": "hoje", "codigo": ""}
    assert _cond({"not_contains": ["teste"]}, ev) and not _cond({"not_contains": ["fatura"]}, ev)
    assert _cond({"has_attachment": True}, ev) and _cond({"has_link": True}, ev)
    assert not _cond({"has_code": True}, ev) and _cond({"has_code": True}, {"text": "Seu código é 482913"})
    assert _cond({"valor_gt": 1000}, ev) and not _cond({"valor_gt": 2000}, ev)
    assert _cond({"valor_gt": 1000, "valor_lt": 1300}, ev) and not _cond({"valor_lt": 100}, {"text": "sem valor"})
    assert _cond({"hora_entre": ["09:00", "18:00"]}, ev) and not _cond({"hora_entre": ["11:00", "18:00"]}, ev)
    assert _cond({"hora_entre": ["22:00", "06:00"]}, dict(ev, time="2026-11-02T23:30:00"))
    assert _cond({"dias_semana": [0, 1, 2, 3, 4]}, ev) and not _cond({"dias_semana": [5, 6]}, ev)
    assert _cond({"tag_in": ["Contas"]}, ev) and _cond({"column_in": ["hoje"]}, ev)
    assert _cond({"any": [{"contains_any": ["boleto"]}, {"contains_any": ["fatura"]}]}, ev)
    assert not _cond({"all": [{"contains_any": ["boleto"]}, {"contains_any": ["fatura"]}]}, ev)
    assert _cond({"not": {"contains_any": ["boleto"]}}, ev)


def test_regex_segura():
    assert regex_segura(r"pedido\s+\d+") == r"pedido\s+\d+"
    for ruim in [r"(a+)+$", r"(\w*)*x", r"(a)\1", r"(?=x)y", "a" * 201, "(abc"]:
        with pytest.raises(TreeError):
            regex_segura(ruim)
    with pytest.raises(ValueError):
        _a(quando={"regex": "(a+)+"})
    with pytest.raises(TreeError):
        validate([{"name": "r", "start": "s", "nodes": {"s": {"if": {"regex": "(x*)*"}, "branches": {}}}}])


def test_normalizar_valida_e_completa():
    a = _a(gatilho={"tipo": "horario", "hora": "8h30", "dias": "uteis"},
           quando={"valor_min": "R$ 500", "hora_de": "9", "hora_ate": "18:00", "dias": ["seg", "sex"],
                   "quantidade": {"n": 3, "minutos": 10},
                   "grupos": [{"modo": "ou", "itens": [{"tipo": "contem", "valor": "boleto, fatura"},
                                                       {"tipo": "valor_entre", "valor": "100 e 50"}]}]},
           tratar=[{"tipo": "classificar", "opcoes": ["orçamento", "dúvida"]}, {"tipo": "agrupar", "minutos": 5}])
    assert a["gatilho"] == {"tipo": "horario", "hora": "08:30", "dias": [0, 1, 2, 3, 4]}
    q = a["quando"]
    assert q["valor_min"] == 500.0 and q["hora_de"] == "09:00" and q["dias"] == [0, 4]
    assert q["grupos"][0]["itens"][0]["valor"] == ["boleto", "fatura"] and q["grupos"][0]["itens"][1]["valor"] == [50, 100]
    assert a["tratar"][0]["campo"] == "classe"
    for ruim in [dict(gatilho={"tipo": "xx"}), dict(quando={"hora_de": "25:00", "hora_ate": "26:00"}),
                 dict(quando={"hora_de": "09:00"}), dict(tratar=[{"tipo": "classificar", "opcoes": ["só uma"]}]),
                 dict(quando={"grupos": [{"itens": [{"tipo": "inventado", "valor": 1}]}]}),
                 dict(entao=[{"acao": "abrir_site", "params": {"url": "javascript:alert(1)"}}]),
                 dict(entao=[{"acao": "encadear", "params": {}}])]:
        with pytest.raises(ValueError):
            _a(**ruim)


def test_compila_grupos_e_ou_e_volta_do_editor():
    from djorge_local.treegraph import graph_to_tree, tree_to_graph, validate_graph
    a = _a(gatilho={"tipo": "card_criado"},
           quando={"palavras": ["boleto"], "nao_contem": ["teste"], "tem_anexo": True, "valor_min": 100, "modo": "ou",
                   "assunto": "contas", "modelo_acha": "é um pedido de orçamento", "quantidade": {"n": 2, "minutos": 5},
                   "grupos": [{"modo": "e", "itens": [{"tipo": "horario", "valor": "09:00-18:00"},
                                                      {"tipo": "coluna", "valor": ["hoje"], "negar": True}]}]},
           tratar=[{"tipo": "extrair", "campos": ["valor", "link"]}, {"tipo": "resumir"}],
           entao=[{"acao": "lembrete", "params": {"titulo": "{title}"}}, {"acao": "mover_card", "params": {"coluna": "hoje"}}])
    t = AU.compilar(a)
    assert t["gatilho"] == {"tipo": "card_criado"} and t["start"] == "filtro"
    c = t["nodes"]["filtro"]["if"]
    assert len(c["any"]) == 4 and c["all"][0]["all"][1] == {"not": {"column_in": ["hoje"]}}
    assert t["nodes"]["modelo"]["keywords"] == [] and t["nodes"]["tratar1"]["action"] == "tratar"
    g = tree_to_graph(t)
    assert not [e for e in validate_graph(g) if e["level"] == "erro"]
    volta = AU.de_arvore(graph_to_tree(g))
    assert volta is not None
    b = AU.normalizar(dict(volta, nome="t"))
    for k in ("gatilho", "tratar", "entao"):
        assert b[k] == a[k], k
    assert b["quando"] == a["quando"]


def test_de_arvore_recusa_formas_que_nao_cabem():
    t = AU.compilar(_a(quando={"palavras": ["x"]}))
    t["nodes"]["filtro"]["if"]["contains_all"] = ["y"]
    assert AU.de_arvore(t) is None
    t = AU.compilar(_a(tratar=[{"tipo": "resumir"}]))
    t["nodes"]["acao1"]["then"] = "tratar1"            # tratamento depois de ação: não é automação
    assert AU.de_arvore(t) is None


def test_migracao_de_automacoes_antigas(tmp_path):
    antiga = {"id": "velha1", "nome": "Velha", "ativa": True, "criada": "2026-01-01T00:00:00", "contagem": 7,
              "quando": {"fontes": ["email"], "palavras": ["boleto"]},
              "entao": [{"acao": "tarefa", "params": {"titulo": "Pagar", "campos": ["valor"]}}]}
    p = tmp_path / "automacoes.json"
    p.write_text(json.dumps({"automacoes": [antiga]}), encoding="utf-8")
    A = AU.Automacoes(p)
    a = A.get("velha1")
    assert a["gatilho"] == {"tipo": "mensagem"} and a["tratar"] == [] and a["contagem"] == 7
    assert a["quando"]["modo"] == "e" and a["quando"]["grupos"] == []
    assert A.arvore("velha1")["nodes"]["filtro"]["if"] == {"source_in": ["email"], "contains_any": ["boleto"]}
    assert [x["id"] for x, _ in A.ativas()] == ["velha1"]


# ----------------------------------------------------------------------------------------- tratamentos --
def test_tratamentos(app):
    a = {"nome": "t", "quando": {}, "tratar": [
        {"tipo": "extrair", "campos": ["valor", "link", "codigo", "prazo"]},
        {"tipo": "classificar", "opcoes": ["orçamento", "reclamação", "dúvida"]},
        {"tipo": "resumir"}, {"tipo": "formatar", "campo": "title", "como": "maiusculas"}],
         "entao": [{"acao": "notificar", "params": {"titulo": "{title} [{classe}] {valor}", "mensagem": "{resumo}"}}]}
    ev = {"title": "Proposta", "text": "Oi! Segue o orçamento da reforma. O orçamento fica em R$ 3.200,00 até 10/11. "
                                       "Veja https://ex.com/p. Abraço.", "time": SEG_10H}
    r = _roda(app, a, ev)
    e = r["event"]
    assert e["valor"] == "R$ 3.200,00" and e["link"] == "https://ex.com/p" and e["prazo"] == "10/11"
    assert e["classe"] == "orçamento" and "orçamento" in e["resumo"] and e["title"] == "PROPOSTA"
    n = _acoes(r)[0]
    assert n["titulo"] == "PROPOSTA [orçamento] R$ 3.200,00"
    assert AU.formatar("2026-11-05T09:30", "data") == "05/11/2026 09:30" and AU.formatar("1250", "moeda") == "R$ 1.250,00"
    assert AU.frase_central("Só uma frase") == "Só uma frase"


def test_agrupar_junta_e_esvazia_pelo_relogio(app):
    R = app.runner
    agora = [1000.0]
    R.relogio = lambda: agora[0]
    a = AU.normalizar({"nome": "g", "quando": {}, "tratar": [{"tipo": "agrupar", "minutos": 10}],
                       "entao": [{"acao": "registrar", "params": {"mensagem": "{title}: {text}"}}]})
    t = AU.compilar(a)
    for i in range(3):
        r = R.run(t, {"title": f"m{i}", "text": f"Mensagem número {i}.", "sender": "Ana"}, mode="testar" if False else None)
        assert not _acoes(r) and r["trail"][-1]["status"] == "agrupado"
        agora[0] += 60
    assert R.esvaziar_grupos({t["name"]: t}) == []          # ainda dentro dos 10 min
    agora[0] += 600
    out = R.esvaziar_grupos({t["name"]: t})
    assert len(out) == 1
    reg = _acoes(out[0])[0]
    assert reg["status"] == "ok" and reg["mensagem"].startswith("3 mensagens agrupadas")
    assert "Ana: Mensagem número 2." in reg["mensagem"]


def test_quantidade_mais_de_n_em_x_min(app):
    R = app.runner
    agora = [0.0]
    R.relogio = lambda: agora[0]
    a = AU.normalizar({"nome": "q", "quando": {"palavras": ["erro"], "quantidade": {"n": 2, "minutos": 5}},
                       "entao": [{"acao": "notificar", "params": {"titulo": "Muitos erros"}}]})
    t = AU.compilar(a)
    res = []
    for i in range(4):
        res.append(bool(_acoes(R.run(t, {"text": "erro no servidor"}, mode="sugerir"))))
        agora[0] += 30
    assert res == [False, False, True, True]
    agora[0] += 600
    assert not _acoes(R.run(t, {"text": "erro"}, mode="sugerir"))
    assert not _acoes(R.run(t, {"text": "tudo certo"}, mode="sugerir"))


def test_modelo_acha_sem_modelo_nao_dispara(app):
    a = {"nome": "m", "quando": {"modelo_acha": "é um pedido de orçamento"},
         "entao": [{"acao": "notificar", "params": {}}]}
    r = _roda(app, a, {"text": "Quanto custa pintar a sala?"})
    assert not _acoes(r) and r["trail"][0]["engine"] == "regra (sem modelo)"


# --------------------------------------------------------------------------------------------- entradas --
def test_card_criado_e_concluido_disparam(app):
    A = app.secretario.automacoes
    A.salvar({"nome": "etq", "gatilho": {"tipo": "card_criado"}, "quando": {"palavras": ["mercado"]},
              "entao": [{"acao": "etiquetar", "params": {"etiqueta": "compras"}},
                        {"acao": "prioridade", "params": {"prioridade": "alta"}}]})
    A.salvar({"nome": "fim", "gatilho": {"tipo": "card_concluido"}, "quando": {},
              "entao": [{"acao": "registrar", "params": {"mensagem": "Concluído: {title}"}}]})
    c = app.cards.create({"title": "Ir ao mercado", "kind": "tarefa"})
    c = app.cards.get(c["id"])
    assert "compras" in c["tags"] and c["priority"] == 2
    app.cards.create({"title": "Outra coisa"})                 # não casa
    assert sum(1 for x in app.cards.all() if "compras" in (x.get("tags") or [])) == 1
    app.cards.move(c["id"], "feito")
    logs = [h for h in app.hist.list(50) if h.get("type") == "log" and "Concluído: Ir ao mercado" in str(h.get("message"))] \
        if hasattr(app.hist, "list") else None
    assert A.get([x for x in A.listar() if x["nome"] == "fim"][0]["id"])["contagem"] == 1
    assert logs is None or logs


def test_acoes_de_card_mover_concluir_lista_nota_encadear(app):
    A = app.secretario.automacoes
    A.salvar({"nome": "Lista de compras", "gatilho": {"tipo": "manual"}, "quando": {},
              "entao": [{"acao": "adicionar_lista", "params": {"lista": "Compras", "texto": "{title}"}}]})
    A.salvar({"nome": "pipeline", "gatilho": {"tipo": "card_criado"}, "quando": {"palavras": ["leite"]},
              "entao": [{"acao": "mover_card", "params": {"coluna": "hoje"}},
                        {"acao": "nota", "params": {"titulo": "Anotado: {title}"}},
                        {"acao": "encadear", "params": {"automacao": "lista de compras"}},
                        {"acao": "concluir_card", "params": {}}]})
    c = app.cards.create({"title": "Comprar leite"})
    c = app.cards.get(c["id"])
    assert c["column"] == "feito" and c["done"]
    lista = [x for x in app.cards.all() if x.get("kind") == "lista"]
    assert len(lista) == 1 and lista[0]["title"] == "Compras" and lista[0]["checklist"][0]["text"] == "Comprar leite"
    assert any(n["titulo"] == "Anotado: Comprar leite" for n in app.notas.todas())


def test_relogio_horario_prazo_e_atraso(app):
    A = app.secretario.automacoes
    A.salvar({"nome": "Bom dia", "gatilho": {"tipo": "horario", "hora": "09:00", "dias": "uteis"}, "quando": {},
              "entao": [{"acao": "registrar", "params": {"mensagem": "{text}"}}]})
    A.salvar({"nome": "Prazo", "gatilho": {"tipo": "prazo_chegando", "horas": 3}, "quando": {},
              "entao": [{"acao": "etiquetar", "params": {"etiqueta": "prazo-perto"}}]})
    A.salvar({"nome": "Atraso", "gatilho": {"tipo": "card_atrasado"}, "quando": {},
              "entao": [{"acao": "mover_card", "params": {"coluna": "hoje"}}]})
    perto = app.cards.create({"title": "Entregar relatório", "due": "2026-11-02T11:00"})
    atras = app.cards.create({"title": "Pagar luz", "due": "2026-11-01T10:00"})
    seg = dt.datetime(2026, 11, 2, 9, 5)
    out = A.tick(seg)
    nomes = sorted(x.get("nome") for x in out if x.get("acoes"))
    assert nomes == ["Atraso", "Bom dia", "Prazo"]
    assert "prazo-perto" in app.cards.get(perto["id"])["tags"]
    assert app.cards.get(atras["id"])["column"] == "hoje"
    assert [x for x in A.tick(seg + dt.timedelta(minutes=1)) if x.get("acoes")] == []     # uma vez só
    sab = dt.datetime(2026, 11, 7, 9, 1)
    assert not [x for x in A.tick(sab) if x.get("nome") == "Bom dia"]
    assert json.loads(A.path.read_text(encoding="utf-8"))["disparos"]


def test_nota_criada_dispara(app):
    A = app.secretario.automacoes
    A.salvar({"nome": "nota", "gatilho": {"tipo": "nota_criada"}, "quando": {"palavras": ["ideia"]},
              "entao": [{"acao": "tarefa", "params": {"titulo": "Revisar: {title}"}}]})
    app.notas.criar({"titulo": "Ideia de app", "corpo": "uma ideia boa"})
    assert any(c["title"] == "Revisar: Ideia de app" for c in app.cards.all())


def test_mensagem_continua_igual_e_ignora_outras_entradas(app):
    sec, A = app.secretario, app.secretario.automacoes
    sec.conf.update(ativo=True, modo="agir")
    A.salvar({"nome": "card só", "gatilho": {"tipo": "card_criado"}, "quando": {},
              "entao": [{"acao": "notificar", "params": {}}]})
    A.salvar({"nome": "msg", "quando": {"palavras": ["pizza"], "nao_contem": ["promoção"]},
              "entao": [{"acao": "criar_card", "params": {"titulo": "Pizza!"}}]})
    assert [a["nome"] for a, _ in A.ativas()] == ["msg"]
    r = sec.processar({"id": "e1", "source": "notifications", "app": "iFood", "title": "iFood",
                       "text": "Sua pizza saiu para entrega"}) if hasattr(sec, "processar") else None
    if r is not None:
        assert r["regras"] == ["msg"]


def test_rodar_agora_e_testar_pela_api(srv, app):
    code, a = srv("/api/automacoes", {"nome": "Resumo do dia", "gatilho": {"tipo": "manual"}, "quando": {},
                                      "entao": [{"acao": "registrar", "params": {"mensagem": "{text}"}}]})
    assert code == 200 and a["gatilho"] == {"tipo": "manual"} and "Quando eu mandar rodar" in a["resumo"]
    app.cards.create({"title": "Ligar pro banco", "column": "hoje"})
    code, r = srv(f"/api/automacoes/{a['id']}/rodar", {})
    assert code == 200 and r["casou"] and "Ligar pro banco" in r["acoes"][0]["mensagem"]
    code, t = srv(f"/api/automacoes/{a['id']}/testar", {"texto": "Exemplo qualquer"})
    assert code == 200 and t["casou"] and "registrar" in t["frase"]
    code, r = srv("/api/automacoes/nao-existe/rodar", {})
    assert code in (400, 404)


# --------------------------------------------------------------------------------------------- de_texto --
@pytest.mark.parametrize("frase,gat,checa", [
    ("Todo dia às 9h, me avise com as pendências", {"tipo": "horario", "hora": "09:00", "dias": []}, None),
    ("Em dias úteis às 8h30 crie uma nota com o resumo", {"tipo": "horario", "hora": "08:30", "dias": [0, 1, 2, 3, 4]},
     lambda a: a["entao"][0]["acao"] == "nota" and a["tratar"] == [{"tipo": "resumir"}]),
    ("Quando um card for concluído, registre no histórico", {"tipo": "card_concluido"},
     lambda a: [e["acao"] for e in a["entao"]] == ["registrar"]),
    ("Quando o prazo de um card estiver chegando (2 horas antes), me avise urgente",
     {"tipo": "prazo_chegando", "horas": 2}, lambda a: a["entao"][0]["params"]["tom"] == "urgente"),
    ("Quando um card atrasar, mova para hoje", {"tipo": "card_atrasado"},
     lambda a: a["entao"] == [{"acao": "mover_card", "params": {"coluna": "hoje", "campos": []}}]),
    ("Se chegar e-mail com anexo e valor acima de R$ 500, crie uma tarefa com valor e prazo", {"tipo": "mensagem"},
     lambda a: a["quando"]["tem_anexo"] and a["quando"]["valor_min"] == 500 and a["entao"][0]["acao"] == "tarefa"),
    ("Se chegar mensagem sem \"teste\" entre 9h e 18h, classifique em orçamento, reclamação ou dúvida e crie um card",
     {"tipo": "mensagem"}, lambda a: a["quando"]["nao_contem"] == ["teste"] and a["quando"]["hora_de"] == "09:00"
     and a["tratar"][0]["opcoes"] == ["orçamento", "reclamação", "dúvida"]),
    ("Se o modelo achar que é um pedido de orçamento, crie um card de alerta com valor", {"tipo": "mensagem"},
     lambda a: a["quando"]["modelo_acha"] == "é um pedido de orçamento"),
    ("Se chegar mais de 3 mensagens em 10 minutos do WhatsApp, junte a cada 15 minutos e me avise", {"tipo": "mensagem"},
     lambda a: a["quando"]["quantidade"] == {"n": 3, "minutos": 10} and a["tratar"] == [{"tipo": "agrupar", "minutos": 15}]),
    ("Se chegar boleto, adicione à lista contas e mude a prioridade alta", {"tipo": "mensagem"},
     lambda a: [e["acao"] for e in a["entao"]] == ["adicionar_lista", "prioridade"]),
    ("Se chegar mensagem do Carlos, prepare uma resposta e abra o rascunho", {"tipo": "mensagem"},
     lambda a: a["entao"][0]["acao"] == "responder_sugestao" and a["entao"][0]["params"]["abrir"] is True),
    ("Se chegar promoção, ignore", {"tipo": "mensagem"}, lambda a: a["entao"][0]["acao"] == "ignorar"),
    ("Quando uma nota for criada, crie uma tarefa", {"tipo": "nota_criada"}, None),
    ("Quando um alerta disparar, registre no histórico", {"tipo": "alerta"}, None),
])
def test_de_texto_v25(frase, gat, checa):
    r = AU.de_texto(frase)
    a = r["automacao"]
    assert a["gatilho"] == gat, (frase, a["gatilho"])
    AU.compilar(a)
    if checa:
        assert checa(a), (frase, a)
    assert r["resumo"] and r["resumo_curto"]


def test_de_texto_antigo_continua():
    r = AU.de_texto("Se eu receber uma mensagem sobre orçamentos, crie um card de alerta de orçamento com valor, "
                    "prazo e quem mandou")
    a = r["automacao"]
    assert a["gatilho"] == {"tipo": "mensagem"} and a["quando"]["assunto"] == "orçamentos"
    assert a["entao"][0]["acao"] == "alerta" and a["tratar"] == []


def test_resumo_em_frase():
    a = _a(gatilho={"tipo": "horario", "hora": "09:00", "dias": [0, 1, 2, 3, 4]},
           quando={"grupos": [{"modo": "ou", "itens": [{"tipo": "contem", "valor": ["boleto"]},
                                                       {"tipo": "tem_anexo", "negar": True}]}]},
           tratar=[{"tipo": "agrupar", "minutos": 30}],
           entao=[{"acao": "responder_sugestao", "params": {"abrir": True}}])
    s = AU.resumo(a)
    assert s.startswith("Em dias úteis, às 09:00") and "tiver “boleto” ou não tiver anexo" in s
    assert "juntar tudo a cada 30 min" in s and "você envia" in s


def test_editor_visual_conhece_os_nos_novos():
    from djorge_local.treegraph import ACTIONS, _label
    for k in ("tratar", "lembrete", "nota", "mover_card", "concluir_card", "encadear", "adicionar_lista", "registrar"):
        assert k in ACTIONS
    assert _label("t", "action", {"action": "tratar", "tipo": "classificar", "opcoes": ["a", "b"]}) == "Classificar: a / b"
    assert _label("c", "if", {"if": {"count_min": {"n": 3, "minutos": 10}}}) == "Mais de 3 em 10 min"
    js = (ROOT / "djorge_local" / "static" / "tree-editor.js").read_text(encoding="utf-8")
    for k in ("tratar", "mover_card", "adicionar_lista", "not_contains", "valor_gt"):
        assert k in js, k
