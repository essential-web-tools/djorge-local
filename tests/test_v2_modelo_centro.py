"""Rodada 3 (M): o modelo no centro. Com o DOFL carregado ele decide (ferramenta, entidades); as regras são pistas,
exceto atalhos explícitos, contas claras, comandos de interface e datas. Preferências (prioridade, nomes) gravadas
em disco; etapas narradas; busca na web só depois de confirmar, com HTML real do DuckDuckGo."""
import datetime as dt
import json
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from djorge.runtime.recognizers import resolver_quando
from djorge.training.flash_data import ASK_TOOL, CAN, TOOL_PT, TOOLS
from djorge_local import web

AGORA = dt.datetime(2026, 11, 2, 10, 0)          # segunda-feira, 10:00


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
    a.command.web_transporte = _offline
    a.search.web_transporte = _offline
    yield a
    a.cards.close()


def _offline(url, timeout):
    raise OSError("sem rede no teste")


class FakeEngine:
    """Imita SemanticEngine.run: NOUL -> p_true, CHOICE -> distribution por opção, resolve -> MatchSet com .matches."""

    def __init__(self, tools, can=0.9, entidades=None):
        self.tools, self.can, self.ent = tools, can, entidades or {}
        self.reqs = []

    def run(self, reqs):
        self.reqs.append(reqs)
        out = []
        for r in reqs:
            if r.kind == "NOUL":
                out.append({"p_true": self.can})
            elif r.kind == "CHOICE":
                assert r.ask == ASK_TOOL["pt"] and list(r.options) == [TOOL_PT[t] for t in TOOLS]
                assert list(r.descriptions) == [TOOLS[t] for t in TOOLS]
                rest = (1 - sum(self.tools.values())) / max(1, len(TOOLS) - len(self.tools))
                out.append({"distribution": {TOOL_PT[t]: self.tools.get(t, rest) for t in TOOLS}})
            elif r.kind == "resolve":
                ms = []
                for alvo, trecho in self.ent.items():
                    i = r.source.casefold().find(trecho.casefold())
                    if alvo == r.target and i >= 0:
                        ms.append(SimpleNamespace(start=i, end=i + len(trecho), probability=0.93, attributes={}))
                out.append(SimpleNamespace(matches=ms))
            else:
                raise AssertionError(r.kind)
        return out


def com_modelo(app, **kw):
    eng = FakeEngine(**kw)
    app.command.engine = lambda: eng
    return eng


# ------------------------------------------------------------------ quem decide --
def test_modelo_decide_e_regras_sao_pistas(app):
    eng = com_modelo(app, tools={"bilhete": 0.86, "cards": 0.08}, entidades={"pessoa": "Ana"})
    r = app.command.run("lembra a Ana de trazer o bolo amanhã")
    assert r["tool"] == "bilhete" and r["decidido_por"] == "modelo" and r["prioridade"] == "modelo"
    assert r["model"] and r["can_do_p"] == 0.9
    # perguntas do treino: CAN, ASK_TOOL e os campos do app (sem datas: datas são das regras)
    reqs = eng.reqs[0]
    assert reqs[0].kind == "NOUL" and reqs[0].options == [CAN["pt"]]
    alvos = [x.target for x in reqs if x.kind == "resolve"]
    assert "pessoa" in alvos and "data" not in alvos and "hora" not in alvos
    # a data continua vindo das regras
    assert r["quando"]["data"] and r["quando"]["achou"]


def test_regras_explicitas_ganham(app):
    com_modelo(app, tools={"cards": 0.95})
    r = app.command.run("#ana@x.com &oi&")
    assert r["tool"] == "bilhete" and r["decidido_por"] == "regras"
    com_modelo(app, tools={"nenhuma": 0.9}, can=0.1)
    r = app.command.run("2+2*3")
    assert r["tool"] == "calcular" and r["proposal"]["value"] == 8 and r["decidido_por"] == "regras"
    eng = com_modelo(app, tools={"buscar": 0.9})
    r = app.command.run("deixa o tema escuro")
    assert r["tool"] == "interface" and r["decidido_por"] == "regras" and eng.reqs == []   # nem chama o modelo
    assert app.ui.get()["theme"] == "escuro"


def test_prioridade_regras_e_equilibrado(app):
    texto = "lembra a Ana de trazer o bolo amanhã"
    app.ui.set({"prioridade": "regras"})
    com_modelo(app, tools={"bilhete": 0.86, "cards": 0.08})
    r = app.command.run(texto)
    assert r["tool"] == "cards" and r["decidido_por"] == "regras" and r["prioridade"] == "regras"
    app.ui.set({"prioridade": "equilibrado"})
    r = app.command.run("me lembra de pagar a luz todo dia 10 às 9h")       # regra de alta certeza
    assert r["tool"] == "cards" and r["decidido_por"] == "regras"
    com_modelo(app, tools={"bilhete": 0.9})
    r = app.command.run("avisa a Ana que vou atrasar")                       # regra fraca: o modelo decide
    assert r["tool"] == "bilhete" and r["decidido_por"] == "modelo"


def test_sem_modelo_decidido_por_regras(app):
    r = app.command.run("me lembra de pagar a luz amanhã às 15h")
    assert r["decidido_por"] == "regras" and r["tool"] == "cards" and not r["model"]


def test_modelo_inseguro_usa_pistas(app):
    com_modelo(app, tools={"cards": 0.3, "bilhete": 0.25, "listas": 0.2})
    r = app.command.run("marca dentista dia 20 às 9h")
    assert r["tool"] == "cards"


# ------------------------------------------------------------------ entidades e web --
DDG_REAL = """<!DOCTYPE html><html><head><title>silvio santos at DuckDuckGo</title></head><body>
<div class="serp__results"><div id="links" class="results">
<div class="result results_links results_links_deep result--ad ">
  <div class="links_main links_deep result__body">
    <h2 class="result__title"><a rel="nofollow" class="result__a" href="https://duckduckgo.com/y.js?ad_domain=loja.com&amp;ad_provider=bingv7aa&amp;u3=x">Compre Já - Loja</a></h2>
    <a class="result__snippet" href="https://duckduckgo.com/y.js?ad_domain=loja.com">Anúncio</a>
  </div>
</div>
<div class="result results_links results_links_deep web-result ">
  <div class="links_main links_deep result__body">
    <h2 class="result__title">
      <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fpt.wikipedia.org%2Fwiki%2FSilvio_Santos&amp;rut=1f2e3d">Silvio <b>Santos</b> &#x2013; Wikipédia, a enciclopédia livre</a>
    </h2>
    <div class="result__extras"><div class="result__extras__url">
      <span class="result__icon"><a rel="nofollow" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fpt.wikipedia.org%2Fwiki%2FSilvio_Santos&amp;rut=1f2e3d"><img class="result__icon__img" width="16" height="16" alt="" src="//external-content.duckduckgo.com/ip3/pt.wikipedia.org.ico" name="i15" /></a></span>
      <a class="result__url" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fpt.wikipedia.org%2Fwiki%2FSilvio_Santos&amp;rut=1f2e3d">pt.wikipedia.org/wiki/Silvio_Santos</a>
    </div></div>
    <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fpt.wikipedia.org%2Fwiki%2FSilvio_Santos&amp;rut=1f2e3d"><b>Silvio</b> <b>Santos</b>, nome artístico de Senor Abravanel, foi um apresentador de televisão e empresário brasileiro.</a>
    <div class="clear"></div>
  </div>
</div>
<div class="result results_links results_links_deep web-result ">
  <div class="links_main links_deep result__body">
    <h2 class="result__title"><a rel="nofollow" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.sbt.com.br%2Fsilvio%3Fa%3D1%26b%3D2&amp;rut=abc" class="result__a">SBT &amp; Silvio</a></h2>
    <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.sbt.com.br%2Fsilvio">A história do <b>dono do baú</b>.</a>
  </div>
</div>
</div></div></body></html>"""
DDG_ANOMALIA = """<html><body><div class="anomaly-modal__title">Unfortunately, bots use DuckDuckGo too.</div>
<form id="challenge-form" action="//duckduckgo.com/anomaly.js"></form></body></html>"""
DDG_LITE = """<table><tr><td valign='top'>1.&nbsp;</td><td><a rel="nofollow" href="https://pt.wikipedia.org/wiki/Silvio_Santos" class='result-link'>Silvio Santos - Wikipédia</a></td></tr>
<tr><td>&nbsp;&nbsp;&nbsp;</td><td class='result-snippet'>Apresentador <b>brasileiro</b>.</td></tr></table>"""


def _ddg(url, timeout):
    if "api.duckduckgo" in url:
        return b'{"Abstract": "", "AbstractText": "", "RelatedTopics": [], "Results": []}'
    return DDG_REAL.encode()


def test_web_html_real():
    r = web.buscar("silvio santos", transporte=_ddg)
    assert [x["url"] for x in r["resultados"]] == ["https://pt.wikipedia.org/wiki/Silvio_Santos",
                                                    "https://www.sbt.com.br/silvio?a=1&b=2"]
    a = r["resultados"][0]
    assert a["titulo"] == "Silvio Santos – Wikipédia, a enciclopédia livre" and a["fonte"] == "pt.wikipedia.org"
    assert a["trecho"].startswith("Silvio Santos, nome artístico de Senor Abravanel")
    assert r["resultados"][1]["titulo"] == "SBT & Silvio" and r["erro"] is None
    assert web._real("//duckduckgo.com/l/?uddg=https%3A%2F%2Fx.com%2Fa%20b&amp;rut=1") == "https://x.com/a b"
    assert "Djorge" not in web.AGENTE and web.AGENTE.startswith("Mozilla/5.0")

    vistos = []

    def bloqueado(url, timeout):                     # HTML pede verificação: cai no lite
        vistos.append(url)
        if "api." in url:
            return b"{}"
        return DDG_ANOMALIA if "html.duckduckgo" in url else DDG_LITE
    r = web.buscar("silvio santos", transporte=bloqueado)
    assert [x["url"] for x in r["resultados"]] == ["https://pt.wikipedia.org/wiki/Silvio_Santos"]
    assert r["resultados"][0]["trecho"] == "Apresentador brasileiro." and any("lite.duckduckgo" in u for u in vistos)
    r = web.buscar("x", transporte=lambda u, t: b"{}" if "api." in u else DDG_ANOMALIA)
    assert r["resultados"] == [] and "internet" in r["erro"]


def test_entidade_do_modelo_e_web_com_confirmacao(app):
    com_modelo(app, tools={"buscar": 0.7}, entidades={"pessoa": "silvio santos"})
    r = app.command.run("Encontre quem foi silvio santos", stream=False)
    assert r["tool"] == "web" and r["needs_confirm"] and r["sai_do_computador"]
    assert r["proposal"] == {"action": "web_search", "q": "Silvio Santos"}
    assert r["reply"] == "Quer que eu busque “Silvio Santos” na internet?" and r["results"] == []
    textos = [x["texto"] for x in app.command.etapas(r["id"])["etapas"]]
    assert "Encontrou “Silvio Santos”" in textos and "Entendeu: buscar na internet" in textos
    app.command.web_transporte = _ddg
    res = app.command.confirm(r["id"], True)
    assert res["reply"] == "Aqui estão resultados sobre “Silvio Santos”."
    assert res["results"][0]["url"] == "https://pt.wikipedia.org/wiki/Silvio_Santos" and res["results"][0]["source"] == "web"
    assert app.command.confirm(r["id"], False)["reply"]                  # depois de feito, não quebra


def test_web_sem_modelo_e_busca_local_oferece_internet(app):
    r = app.command.run("encontre quem foi silvio santos")
    assert r["proposal"] == {"action": "web_search", "q": "Silvio Santos"} and r["decidido_por"] == "regras"
    assert not app.command.confirm(r["id"], False).get("results")       # recusar: nada sai do computador
    app.cards.create({"title": "Boleto da escola"})
    r = app.command.run("procura o boleto")
    assert r["tool"] == "buscar" and r["reply"].startswith("Aqui estão 1 resultado sobre “boleto”")
    r = app.command.run("procura zzqx")
    assert r["tool"] == "web" and r["needs_confirm"] and r["proposal"]["action"] == "web_search"
    assert "nos seus dados" in r["reply"] and "na internet" in r["reply"]


# ------------------------------------------------------------------ etapas narradas --
def test_etapas_com_nomes(app):
    app.ui.set({"nome_agente": "Jorge", "nome_usuario": "Arthur"})
    K = app.command
    r = K.run("reunião com a Ana amanhã às 14h")
    e = [x["texto"] for x in K.etapas(r["id"])["etapas"]]
    assert e[0] == "Lendo o seu pedido…" and e[1] == "Jorge está entendendo o que Arthur quer…"
    assert "Reconheceu a data ter., 3 de nov. às 14:00" in e or any(x.startswith("Reconheceu a data") for x in e)
    assert "Entendeu: criar um evento" in e and "Jorge está usando a ferramenta Cards…" in e and e[-1] == "Pronto"
    assert len(e) == len(set(e))                                            # uma linha por acontecimento
    assert r["agente"] == "Jorge"
    r = K.run("toda terça e quinta às 19h academia")
    e = [x["texto"] for x in K.etapas(r["id"])["etapas"]]
    assert "Reconheceu a repetição toda terça e quinta às 19:00" in e


# ------------------------------------------------------------------ preferências gravadas --
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


def test_uiprefs_persistem(srv, app):
    from djorge_local.uiprefs import UIPrefs
    p = app.ui.get()
    assert (p["prioridade"], p["nome_usuario"], p["nome_agente"]) == ("modelo", "", "Djorge")
    # o front manda os nomes dele (dark, compact, columns, voice) e às vezes chaves que o servidor não conhece
    code, r = srv("/api/ui", {"theme": "dark", "density": "compact", "font_size": "lg", "columns": ["entrada", "hoje"],
                              "voice": True, "default_view": "quadro", "prioridade": "equilibrado",
                              "nome_usuario": "  Arthur  ", "nome_agente": "Jorge", "_server": {}, "coisa_nova": 1})
    assert code == 200
    assert r["prefs"]["theme"] == "escuro" and r["prefs"]["density"] == "compacta" and r["prefs"]["font_size"] == "grande"
    assert r["prefs"]["kanban_columns"] == ["entrada", "hoje"] and r["prefs"]["voice_enabled"] is True
    assert r["prefs"]["default_view"] == "kanban" and r["prefs"]["nome_usuario"] == "Arthur"
    # relido do disco por outra instância (como ao reabrir o app)
    d = UIPrefs(app.ui.path).get()
    assert (d["prioridade"], d["nome_agente"], d["theme"]) == ("equilibrado", "Jorge", "escuro")
    code, g = srv("/api/ui")
    assert g["prefs"]["prioridade"] == "equilibrado" and "modelo" in g["choices"]["prioridade"]
    assert srv("/api/ui", {"prioridade": "chute"})[0] == 400
    assert srv("/api/ui", {"so_desconhecida": 1})[0] == 400
    assert srv("/api/ui", {"default_view": "mensagens"})[1]["prefs"]["default_view"] == "mensagens"
    assert app.ui.parse("me chama de arthur")["patch"] == {"nome_usuario": "Arthur"}
    assert app.ui.parse("seu nome agora é Jorge")["patch"] == {"nome_agente": "Jorge"}
    assert app.ui.parse("prefiro respostas rápidas")["patch"] == {"prioridade": "regras"}


# ------------------------------------------------------------------ datas coloquiais --
@pytest.mark.parametrize("texto,data,hora", [
    ("depois de amanhã de manhã", "2026-11-04", "09:00"),
    ("no dia 3", "2026-11-03", None),
    ("dia 1º", "2026-12-01", None),
    ("1º de maio", "2027-05-01", None),
    ("às 7 da noite", "2026-11-02", "19:00"),
    ("meio-dia e meia", "2026-11-02", "12:30"),
    ("daqui a pouco", "2026-11-02", "10:30"),
    ("daqui a pouquinho", "2026-11-02", "10:15"),
    ("início do mês que vem", "2026-12-01", None),
    ("meio do mês que vem", "2026-12-15", None),
    ("começo do mês", "2026-12-01", None),
    ("dia 10 do mês que vem", "2026-12-10", None),
    ("em 2 semanas", "2026-11-16", None),
    ("em 3 dias às 9h", "2026-11-05", "09:00"),
    ("sexta que vem às 8 da manhã", "2026-11-06", "08:00"),
    ("no natal", "2026-12-25", None),
    ("véspera de natal às 20h", "2026-12-24", "20:00"),
    ("ano novo", "2027-01-01", None),
    ("dia de tiradentes", "2027-04-21", None),
    ("feriado de finados", "2026-11-02", None),
    ("proclamação da república", "2026-11-15", None),
    ("consciência negra", "2026-11-20", None),
    ("dia das crianças", "2027-10-12", None),
])
def test_datas_coloquiais(texto, data, hora):
    q = resolver_quando(texto, agora=AGORA)
    assert (q["data"], q["hora"]) == (data, hora), (texto, q)


def test_recorrencia_e_prazo():
    q = resolver_quando("toda terça e quinta às 19h", agora=AGORA)
    assert q["recorrencia"] == {"freq": "semanal", "dias_semana": [1, 3], "hora": "19:00"} and q["inicio"] == "2026-11-03T19:00"
    assert resolver_quando("a cada 15 dias", agora=AGORA)["recorrencia"] == {"freq": "diaria", "intervalo": 15}
    q = resolver_quando("entregar o relatório até sexta", agora=AGORA)
    assert q["prazo"] and q["data"] == "2026-11-06"
    assert not resolver_quando("reunião sexta", agora=AGORA)["prazo"]


def test_card_com_prazo_vira_tarefa(app):
    r = app.command.run("entregar o relatório até sexta")
    c = r["results"][0]
    assert c["kind"] == "tarefa" and c["due"] and r["tool"] == "cards"
    e = [x["texto"] for x in app.command.etapas(r["id"])["etapas"]]
    assert any(x.startswith("Reconheceu o prazo ") for x in e) and "Entendeu: criar uma tarefa" in e
