"""Secretário (rodada 2): automações, receitas, notificações, secretário sugerir/agir/desfazer, silêncio, contatos,
abrir_app (com mock), histórico, API local, pausar/retomar, início e comando com etapas.

Pedido ao F (models.py), opcional: `Models.stop_api(name)` que desliga a API HTTP de um modelo sem descarregá-lo,
e respeitar `cfg["api"]["desligadas"]` (lista de nomes) em `_start_api` para não religar ao carregar. Sem isso,
app.py desliga parando o servidor em `Models._servers` (getattr protegido).
"""
import datetime as dt
import json
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ORC = ("Olá! Segue o orçamento da reforma da cozinha: R$ 1.250,00, válido até 10/11. "
       "Qualquer dúvida me chame.")


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
    a.cards.close()
    a.secretario.close()


@pytest.fixture()
def srv(app):
    from djorge_local.app import serve
    httpd = serve(app, block=False)
    base = f"http://127.0.0.1:{app.cfg['server']['port']}"
    tok = app.cfg["server"]["token"]

    def call(path, body=None):
        h = {"Content-Type": "application/json", "X-Token": tok}
        req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(), headers=h)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            raw = e.read()
            return e.code, (json.loads(raw) if raw[:1] == b"{" else raw)
    yield call
    httpd.shutdown()


def _ev(text, title="", app="WhatsApp", sender="", source="notifications"):
    return {"id": "x" + str(time.time_ns()), "source": source, "text": text, "title": title, "app": app,
            "sender": sender, "time": time.strftime("%Y-%m-%dT%H:%M:%S")}


# ------------------------------------------------------------------------------------------- automações --
def test_de_texto_orcamento_e_compilacao():
    from djorge_local.automacoes import compilar, de_texto
    r = de_texto("Se eu receber uma mensagem sobre orçamentos, crie um card de alerta de orçamento com valor, "
                 "prazo e quem mandou")
    a = r["automacao"]
    assert a["quando"]["assunto"] == "orçamentos"
    assert a["entao"][0]["acao"] == "alerta"
    p = a["entao"][0]["params"]
    assert p["campos"] == ["valor", "prazo", "remetente"] and "{remetente}" in p["titulo"]
    assert p["titulo"].startswith("Alerta de orçamento")
    assert "orçamentos" in r["resumo"] and "quem mandou" in r["resumo"]
    t = compilar(a)
    assert t["nodes"]["assunto"]["keywords"] == ["orcament"]
    r2 = de_texto("Quando chegar e-mail da Ana, crie uma tarefa")
    assert r2["automacao"]["quando"]["fontes"] == ["email"] and r2["automacao"]["quando"]["remetente"] == "Ana"
    with pytest.raises(ValueError):
        de_texto("oi")


def test_automacao_orcamento_cria_card_alerta_agir(app):
    from djorge_local.automacoes import de_texto
    sec = app.secretario
    sec.configurar({"ativo": True, "modo": "agir"})
    a = sec.automacoes.salvar(de_texto("Se eu receber uma mensagem sobre orçamentos, crie um card de alerta de "
                                       "orçamento com valor, prazo e quem mandou")["automacao"])
    out = app.handle(_ev(ORC, title="Orçamento cozinha", app="Outlook", sender="Carlos Souza <carlos@alfa.com.br>",
                         source="email"))["secretario"]
    assert out["regras"] == [a["nome"]] and out["card_id"]
    c = app.cards.get(out["card_id"])
    assert c["title"] == "Alerta de orçamento — Carlos Souza"
    assert "Valor: R$ 1.250,00" in c["body"] and "Prazo: ter., 10 de nov." in c["body"]
    assert "Quem mandou: Carlos Souza" in c["body"] and "alerta" in c["tags"] and c["due"].startswith("2026-11-10")
    assert sec.automacoes.get(a["id"])["contagem"] == 1
    n = sec.store.get(out["notificacao"])
    assert n["card_id"] == c["id"] and n["regras"] == [a["nome"]]
    # desfazer apaga o card
    r = sec.acao(n["id"], "desfazer")
    assert "alerta" in r["desfeitas"] and app.cards.get(c["id"]) is None
    # mensagem sem relação: nada
    out2 = app.handle(_ev("Bom dia, tudo bem?", title="Ana"))["secretario"]
    assert out2["regras"] == [] and not out2["acoes"]


def test_sugerir_aprovar_e_recusar(app):
    from djorge_local.automacoes import de_texto
    sec = app.secretario
    sec.configurar({"ativo": True})
    assert sec.conf["modo"] == "sugerir"
    sec.automacoes.salvar(de_texto("Se eu receber uma mensagem sobre orçamentos, crie um card de alerta com valor")
                          ["automacao"])
    out = app.handle(_ev(ORC, title="Carlos"))["secretario"]
    assert out["sugestoes"] and not out["card_id"] and not app.cards.all()
    s0 = out["sugestoes"][0]
    assert s0["status"] == "sugerido" and s0["card"]["title"].startswith("Alerta de orçamento")
    r = sec.acao(out["notificacao"], "aprovar", {"i": 0})
    assert r["feitas"][0]["card_id"] and len(app.cards.all()) == 1
    assert r["notificacao"]["sugestoes"] == [] and r["notificacao"]["lida"]
    out = app.handle(_ev(ORC, title="Carlos"))["secretario"]
    r = sec.acao(out["notificacao"], "recusar", {"i": 0})
    assert r["notificacao"]["sugestoes"] == [] and len(app.cards.all()) == 1


def test_receitas(app):
    from djorge_local.automacoes import Automacoes, compilar, normalizar
    ms = Automacoes.modelos()
    ids = {m["id"] for m in ms}
    assert {"boletos", "orcamentos", "entregas", "reunioes", "codigos", "vip", "promocoes"} <= ids
    for m in ms:
        compilar(normalizar(m))
        assert m["resumo"].startswith("Se chegar")
    sec = app.secretario
    sec.configurar({"ativo": True, "modo": "agir", "vips": ["Marta Chefe"]})
    for rid in ("boletos", "promocoes", "vip", "codigos"):
        a = next(m for m in ms if m["id"] == rid)
        sec.automacoes.salvar({k: a[k] for k in ("nome", "quando", "entao")})
    out = app.handle(_ev("Sua fatura de R$ 230,00 vence em 05/11. Código de barras 1234", title="Enel",
                         app="Gmail", sender="cobranca@enel.com.br", source="email"))["secretario"]
    c = app.cards.get(out["card_id"])
    assert c["kind"] == "tarefa" and c["title"].startswith("Pagar") and "R$ 230,00" in c["body"]
    assert sec.store.get(out["notificacao"])["etiqueta"] == "financeiro"
    out = app.handle(_ev("Black friday: 50% de desconto com cupom! Frete grátis só hoje", title="Loja",
                         sender="news@loja.com", source="email"))["secretario"]
    assert sec.store.get(out["notificacao"])["arquivada"]
    out = app.handle(_ev("Preciso daquele relatório", title="Marta Chefe"))["secretario"]
    assert any(x["action"] == "alerta" for x in out["acoes"])
    assert app.cards.get(out["card_id"])["priority"] == 3


def test_testar_nao_executa(app, srv):
    code, r = srv("/api/automacoes/de_texto", {"texto": "Se eu receber uma mensagem sobre orçamentos, crie um card "
                                                        "de alerta de orçamento com valor, prazo e quem mandou",
                                               "salvar": True})
    assert code == 200 and r["automacao"]["id"] and r["arvore"]["start"] == "assunto" and r["modelo"] is False
    aid = r["automacao"]["id"]
    code, t = srv(f"/api/automacoes/{aid}/testar", {"texto": ORC, "remetente": "Carlos Souza <c@alfa.com.br>"})
    assert code == 200 and t["casou"] and t["acoes"][0]["status"] == "simulado"
    assert t["acoes"][0]["card"]["title"] == "Alerta de orçamento — Carlos Souza"
    assert t["campos"]["valor"] == "R$ 1.250,00" and "criaria o card" in t["frase"]
    assert app.cards.all() == []
    code, t = srv(f"/api/automacoes/{aid}/testar", {"texto": "Vamos almoçar amanhã?"})
    assert not t["casou"] and "não faria nada" in t["frase"]
    code, r = srv("/api/automacoes")
    assert len(r["automacoes"]) == 1 and r["automacoes"][0]["resumo"]
    code, r = srv("/api/automacoes/modelos")
    assert len(r["modelos"]) >= 7
    code, r = srv("/api/automacoes", {"modelo": "entregas"})
    assert code == 200 and r["nome"] == "Entregas"
    code, r = srv(f"/api/automacoes/{aid}/arvore")
    assert code == 200 and r["tree"]["nodes"]["acao1"]["action"] == "alerta"
    code, r = srv(f"/api/automacoes/{aid}/desativar", {})
    assert r["ativa"] is False
    code, r = srv(f"/api/automacoes/{aid}/delete", {})
    assert r["ok"] is True
    code, r = srv("/api/automacoes", {"nome": "x", "entao": [{"acao": "explodir"}]})
    assert code == 400


# ------------------------------------------------------------------------------------------ notificações --
def test_notificacoes_store_e_rotas(app, srv):
    st = app.secretario.store
    a = st.add(_ev("Pedido 123 saiu para entrega", title="Correios", app="Chrome"), etiqueta="compras e entregas")
    st.add(_ev("Oi sumida", title="Bia"), etiqueta="pessoal", prioridade="alta")
    assert [x["etiqueta"] for x in st.etiquetas()] == ["compras e entregas", "pessoal"] or len(st.etiquetas()) == 2
    code, r = srv("/api/notificacoes?etiqueta=pessoal")
    assert code == 200 and [n["titulo"] for n in r["notificacoes"]] == ["Bia"]
    code, r = srv("/api/notificacoes?q=entrega")
    assert len(r["notificacoes"]) == 1
    code, r = srv(f"/api/notificacoes/{a['id']}", {"lida": True, "etiqueta": "entregas"})
    assert r["lida"] and r["etiqueta"] == "entregas"
    code, r = srv("/api/notificacoes?lida=0")
    assert [n["titulo"] for n in r["notificacoes"]] == ["Bia"]
    code, r = srv(f"/api/notificacoes/{a['id']}/acao", {"acao": "arquivar"})
    assert r["notificacao"]["arquivada"]
    code, r = srv("/api/notificacoes")
    assert len(r["notificacoes"]) == 1
    code, r = srv("/api/notificacoes/etiquetas")
    assert code == 200 and r["etiquetas"]
    code, r = srv("/api/notificacoes/limpar", {"antes_de": "2000-01-01"})
    assert r["apagadas"] == 0
    code, r = srv("/api/notificacoes/limpar", {})
    assert r["apagadas"] == 2 and st.list(arquivada="todas") == []
    # evento pelo /api/event (sync) entra no store mesmo com o secretário desligado
    code, r = srv("/api/event", {"sync": True, "source": "notifications", "text": "Reunião amanhã 14h",
                                 "title": "Ana", "app": "Teams"})
    assert r["secretario"]["notificacao"] and r["secretario"]["acoes"] == []
    assert st.get(r["secretario"]["notificacao"])["app"] == "Teams"


def test_secretario_config_ignorados_e_silencio(app, srv):
    sec = app.secretario
    code, c = srv("/api/secretario")
    assert code == 200 and c["ativo"] is False and c["modo"] == "sugerir" and "frase" in c["resumo"]
    code, c = srv("/api/secretario", {"ativo": True, "modo": "agir", "apps_ignorados": "Spotify, Steam",
                                      "horario_silencio": "22:00-07:00", "vips": ["Marta"]})
    assert c["apps_ignorados"] == ["Spotify", "Steam"] and c["horario_silencio"] == {"ativo": True, "inicio": "22:00",
                                                                                    "fim": "07:00"}
    assert c["rodando"] is True
    code, r = srv("/api/secretario", {"modo": "mandar"})
    assert code == 400
    assert sec.em_silencio(dt.datetime(2026, 11, 2, 23, 30)) and sec.em_silencio(dt.datetime(2026, 11, 3, 6, 59))
    assert not sec.em_silencio(dt.datetime(2026, 11, 2, 12, 0))
    assert app.handle(_ev("Nova música", app="Spotify"))["secretario"] == {"ignorado": True}
    # silêncio: o alerta vira card, mas sem toast
    from djorge_local import toasts
    sec.agora = lambda: dt.datetime(2026, 11, 2, 23, 0)
    out = app.handle(_ev("Preciso falar com você", title="Marta"))["secretario"]
    alerta = next(x for x in out["acoes"] if x["action"] == "alerta")
    assert alerta["toast"] == "silenciado" and alerta["card_id"]
    sec.agora = lambda: dt.datetime(2026, 11, 2, 12, 0)
    n0 = len(toasts.ULTIMOS)
    out = app.handle(_ev("Preciso falar com você", title="Marta"))["secretario"]
    assert next(x for x in out["acoes"] if x["action"] == "alerta")["toast"] in ("ok", "sem toast")
    assert len(toasts.ULTIMOS) == n0 + 1
    srv("/api/secretario", {"ativo": False})
    assert not sec.conf["ativo"]


def test_sem_sinais_nao_faz_nada_e_sinais_sugerem(app):
    sec = app.secretario
    sec.configurar({"ativo": True})
    out = app.handle(_ev("kkkk boa", title="Léo"))["secretario"]
    assert out["sugestoes"] == [] and out["acoes"] == []
    out = app.handle(_ev("Sua fatura de R$ 99,90 vence em 05/11", title="Banco", sender="fatura@banco.com",
                         source="email"))["secretario"]
    assert [s["action"] for s in out["sugestoes"]] == ["tarefa"]
    r = sec.resumo_diario(dt.date.today())
    assert r["total"] >= 2 and "sugest" in r["frase"]


def test_toasts_fallback_silencioso():
    from djorge_local import toasts
    r = toasts.mostrar("Teste", "olá")                     # no Linux não mostra; nunca quebra
    assert r is (False if sys.platform != "win32" else r)
    assert toasts.status()["ultimos"][0]["titulo"] == "Teste"


# --------------------------------------------------------------------------------------------- contatos --
def test_contatos(app, srv):
    app.cards.create({"title": "Reunião", "people": [{"nome": "ana paula", "tipo": "pessoa", "email": "ana@x.com"}]})
    app.cards.create({"title": "Outra", "people": [{"nome": "Ana Paula", "tipo": "pessoa", "email": "ana@x.com"}]})
    app.secretario.store.add(_ev("oi", sender="Bruno Lima <bruno@y.com>", source="email"))
    code, r = srv("/api/contatos")
    nomes = {c["nome"]: c for c in r["contatos"]}
    assert nomes["Ana Paula"]["email"] == "ana@x.com" and nomes["Ana Paula"]["vezes"] == 2
    assert nomes["Bruno Lima"]["email"] == "bruno@y.com"
    code, r = srv("/api/contatos?q=bru")
    assert [c["nome"] for c in r["contatos"]] == ["Bruno Lima"]
    from djorge_local.contatos import fone_fmt, nome_fmt
    assert nome_fmt("JOÃO DA SILVA") == "João da Silva" and fone_fmt("+55 11 98765-4321") == "(11) 98765-4321"


# --------------------------------------------------------------------------------------------- abrir_app --
def test_abrir_app_mock(app, srv, monkeypatch):
    from djorge_local import actions
    abertos = []
    monkeypatch.setattr(actions, "_open_url", lambda u: abertos.append(u) or True)
    r = actions.abrir_app({"app": "whatsapp", "para": "(11) 98765-4321", "texto": "Oi, tudo bem?"})
    assert r["url"] == "whatsapp://send?phone=5511987654321&text=Oi%2C%20tudo%20bem%3F" and r["status"] == "aberto"
    r = actions.link_app({"app": "wa.me", "para": "5511987654321", "texto": "Oi"})
    assert r["url"] == "https://wa.me/5511987654321?text=Oi"
    r = actions.link_app({"app": "email", "para": "ana@x.com", "assunto": "Orçamento", "texto": "Olá"})
    assert r["url"].startswith("mailto:ana@x.com?subject=Or%C3%A7amento&body=Ol%C3%A1")
    with pytest.raises(ValueError):
        actions.link_app({"app": "navegador", "url": "javascript:alert(1)"})
    code, r = srv("/api/abrir", {"app": "navegador", "url": "https://exemplo.com"})
    assert code == 200 and abertos[-1] == "https://exemplo.com"
    # desktop não instalado -> cai no WhatsApp Web
    monkeypatch.setattr(actions, "_open_url", lambda u: (abertos.append(u) or True) if u.startswith("https") else False)
    r = actions.abrir_app({"app": "whatsapp", "para": "11987654321", "texto": "x"})
    assert r["url"].startswith("https://wa.me/5511987654321")
    # como ação de automação (modo agir): abre com o telefone da mensagem
    sec = app.secretario
    sec.configurar({"ativo": True, "modo": "agir"})
    sec.automacoes.salvar({"nome": "zap", "quando": {"palavras": ["me liga"]},
                           "entao": [{"acao": "abrir_app", "params": {"app": "wa.me", "texto": "Já te ligo!"}}]})
    out = app.handle(_ev("me liga no 11 98765-4321", title="Rui"))["secretario"]
    assert out["acoes"][0]["action"] == "abrir_app" and "5511987654321" in out["acoes"][0]["url"]


# --------------------------------------------------------------------------------------------- histórico --
def test_historico_apagar(app, srv):
    for i in range(3):
        app.hist.add({"type": "log", "message": f"m{i}"})
    items = app.hist.last()
    code, r = srv(f"/api/history/{items[0]['id']}/delete", {})
    assert r["apagados"] == 1 and len(app.hist.last()) == 2
    code, r = srv("/api/history/clear", {"antes_de": "2000-01-01"})
    assert r["apagados"] == 0
    code, r = srv("/api/history/clear", {"ids": [app.hist.last()[0]["id"]]})
    assert r["apagados"] == 1
    code, r = srv("/api/history/clear", {"tudo": True})
    assert app.hist.last() == [] and app.hist.file.read_text(encoding="utf-8") == ""
    code, r = srv("/api/history/clear", {})
    assert code == 400


# ----------------------------------------------------------------------------- API local / leveza / início --
def test_api_local_pausar_retomar_recursos(app, srv, monkeypatch):
    code, r = srv("/api/local_api")
    assert code == 200 and {x["modelo"] for x in r["apis"]} >= {"flash"} and r["apis"][0]["ativa"] is False
    assert "curl" in r["apis"][0]["exemplo"]
    # modelo "carregado" falso: ligar chama _start_api, desligar para o servidor
    started = []

    class Srv:
        def shutdown(self): started.append("parou")
        def server_close(self): pass
    monkeypatch.setattr(app.models, "loaded", lambda: ["flash"])
    monkeypatch.setattr(app.models, "get", lambda n="flash": object())

    def fake_start(name, z):
        app.models.apis[name] = "http://127.0.0.1:9999"; app.models._servers[name] = Srv(); started.append(name)
    monkeypatch.setattr(app.models, "_start_api", fake_start)
    code, r = srv("/api/local_api", {"modelo": "flash", "ativa": True, "porta": 9999})
    assert r["ativa"] and r["porta"] == 9999 and started == ["flash"]
    code, r = srv("/api/local_api", {"modelo": "flash", "ativa": False})
    assert r["ativa"] is False and "flash" in app.cfg["api"]["desligadas"]
    code, r = srv("/api/local_api", {"modelo": "nada", "ativa": True})
    assert code == 400
    monkeypatch.undo()
    code, r = srv("/api/app/recursos")
    assert code == 200 and "memoria_mb" in r and r["pausado"] is False
    code, r = srv("/api/app/pausar", {})
    assert r["pausado"] and app.pausado
    code, r = srv("/api/app/retomar", {})
    assert r["pausado"] is False
    code, r = srv("/api/app/liberar_memoria", {})
    assert code == 200 and r["ok"]


def test_inicio_e_comando_com_etapas(app, srv):
    hoje = dt.date.today().isoformat()
    app.cards.create({"title": "Pagar luz", "kind": "tarefa", "due": hoje, "priority": 2})
    app.cards.create({"title": "Dentista", "kind": "evento",
                      "start": (dt.datetime.now() + dt.timedelta(days=1)).strftime("%Y-%m-%dT09:00")})
    app.secretario.store.add(_ev("urgente!", title="Chefe"), prioridade="urgente")
    code, r = srv("/api/inicio")
    assert code == 200 and r["tarefas_relevantes"][0]["title"] == "Pagar luz"
    assert r["proximos_eventos"][0]["title"] == "Dentista" and r["mensagens_importantes"][0]["titulo"] == "Chefe"
    assert "Você tem 1 tarefa para hoje" in r["resumo_frase"] and "Dentista" in r["resumo_frase"]
    code, r = srv("/api/command", {"text": "quanto é 2 + 2", "stream": True})
    assert code == 200 and r["id"]
    for _ in range(100):
        code, e = srv(f"/api/command/{r['id']}/etapas")
        if e["pronto"]:
            break
        time.sleep(0.05)
    assert e["pronto"] and e["etapas"][0]["texto"] == "Lendo o seu pedido…" and "resultado" in e
    code, e = srv("/api/command/nada/etapas")
    assert code == 404
    code, w = srv("/api/web?q=")                       # sem rede no teste: só a validação
    assert code == 400
