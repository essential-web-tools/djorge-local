"""Rodada 2 (R): pistas + decidir, regras de data/hora/recorrência, dicas, formatação, datas humanas nos cards,
busca na web com transporte falso, abrir app, etapas do comando e confirmação trocando o tipo da proposta."""
import datetime as dt
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from djorge.runtime.recognizers import (data_humana, descrever_recorrencia, proxima, reconhecer, recorrencia,
                                        resolver_quando)
from djorge_local import formatar as F
from djorge_local import pistas as P
from djorge_local import web

AGORA = dt.datetime(2026, 11, 2, 10, 0)          # segunda-feira, 10:00


def Q(texto, agora=AGORA):
    return resolver_quando(texto, agora=agora)


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DJORGE_LOCAL_HOME", str(tmp_path))
    import importlib
    import yaml
    import djorge_local.config as C
    importlib.reload(C)
    (tmp_path / "arvores.yaml").write_text("trees: []\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({
        "device": "cpu", "models": {"flash": {"path": ""}}, "server": {"port": 18765},
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


# ------------------------------------------------------------------ datas, horas e recorrência --
@pytest.mark.parametrize("texto,data,hora", [
    ("dia 15 de dezembro", "2026-12-15", None),
    ("15/11 às 3 da tarde", "2026-11-15", "15:00"),
    ("em 15/11", "2026-11-15", None),
    ("15 de nov", "2026-11-15", None),
    ("3 de jan.", "2027-01-03", None),
    ("amanhã às 9h", "2026-11-03", "09:00"),
    ("amanhã cedo", "2026-11-03", "07:00"),
    ("amanhã de manhã às 9", "2026-11-03", "09:00"),
    ("depois de amanhã às 14:30", "2026-11-04", "14:30"),
    ("daqui a 3 horas", "2026-11-02", "13:00"),
    ("daqui a 20 min", "2026-11-02", "10:20"),
    ("em meia hora", "2026-11-02", "10:30"),
    ("daqui a 2 dias", "2026-11-04", None),
    ("semana que vem na terça", "2026-11-10", None),
    ("terça da semana que vem às 10h", "2026-11-10", "10:00"),
    ("sexta às 18h", "2026-11-06", "18:00"),
    ("próxima quinta", "2026-11-05", None),
    ("fim do mês", "2026-11-30", None),
    ("próximo dia útil", "2026-11-03", None),
    ("hoje às 3", "2026-11-02", "15:00"),
    ("hoje às 8", "2026-11-02", "08:00"),
    ("hoje no meio da tarde", "2026-11-02", "15:00"),
    ("hoje à noite", "2026-11-02", "19:00"),
    ("meio-dia", "2026-11-02", "12:00"),
    ("2026-12-01", "2026-12-01", None),
    ("03/11/2026 14h", "2026-11-03", "14:00"),
    ("dia 5", "2026-11-05", None),
    ("dia 1", "2026-12-01", None),
])
def test_datas_e_horas(texto, data, hora):
    q = Q(texto)
    assert q["data"] == data, (texto, q)
    assert q["hora"] == hora, (texto, q)


def test_intervalo_e_duracao():
    q = Q("reunião das 14h às 16h")
    assert (q["hora"], q["fim"], q["termino"]) == ("14:00", "16:00", "2026-11-02T16:00")
    q = Q("amanhã das 2 às 4 da tarde")
    assert (q["data"], q["hora"], q["fim"]) == ("2026-11-03", "14:00", "16:00")
    q = Q("call de 9h a 10h30 sexta")
    assert (q["data"], q["hora"], q["fim"]) == ("2026-11-06", "09:00", "10:30")
    q = Q("aula de 2 horas amanhã às 10h")
    assert q["duracao_min"] == 120 and q["termino"] == "2026-11-03T12:00"
    assert Q("treino por 45 min hoje às 18h")["termino"] == "2026-11-02T18:45"


@pytest.mark.parametrize("texto,rec,primeira", [
    ("todo dia 20", {"freq": "mensal", "dia": 20}, "2026-11-20"),
    ("todo dia 20 às 9h", {"freq": "mensal", "dia": 20, "hora": "09:00"}, "2026-11-20T09:00"),
    ("todo dia 5 às 9h", {"freq": "mensal", "dia": 5, "hora": "09:00"}, "2026-11-05T09:00"),
    ("todo dia 2 às 8h", {"freq": "mensal", "dia": 2, "hora": "08:00"}, "2026-12-02T08:00"),
    ("toda primeira segunda do mês", {"freq": "mensal", "ordinal": 1, "dias_semana": [0]}, "2026-11-02"),
    ("toda primeira segunda do mês às 9h", {"freq": "mensal", "ordinal": 1, "dias_semana": [0], "hora": "09:00"},
     "2026-12-07T09:00"),
    ("toda última sexta do mês", {"freq": "mensal", "ordinal": -1, "dias_semana": [4]}, "2026-11-27"),
    ("dias úteis às 8h", {"freq": "dias_uteis", "hora": "08:00"}, "2026-11-03T08:00"),
    ("todos os dias úteis às 11h", {"freq": "dias_uteis", "hora": "11:00"}, "2026-11-02T11:00"),
    ("a cada 2 semanas", {"freq": "semanal", "intervalo": 2}, "2026-11-02"),
    ("a cada 3 dias às 7h", {"freq": "diaria", "intervalo": 3, "hora": "07:00"}, "2026-11-05T07:00"),
    ("toda segunda e quarta às 7h", {"freq": "semanal", "dias_semana": [0, 2], "hora": "07:00"}, "2026-11-04T07:00"),
    ("todo sábado", {"freq": "semanal", "dias_semana": [5]}, "2026-11-07"),
    ("todo fim de mês", {"freq": "mensal", "ultimo_dia": True}, "2026-11-30"),
    ("todo dia 15 de março", {"freq": "anual", "dia": 15, "mes": 3}, "2027-03-15"),
    ("todos os dias às 22h", {"freq": "diaria", "hora": "22:00"}, "2026-11-02T22:00"),
    ("diariamente às 9h", {"freq": "diaria", "hora": "09:00"}, "2026-11-03T09:00"),
    ("dia sim, dia não", {"freq": "diaria", "intervalo": 2}, "2026-11-02"),
])
def test_recorrencia(texto, rec, primeira):
    q = Q(texto)
    assert q["recorrencia"] == rec, (texto, q)
    assert q["inicio"] == primeira, (texto, q)
    assert q["recorrencia_txt"]


def test_proxima_e_descricao():
    r = {"freq": "mensal", "dia": 31}
    assert proxima(r, dt.datetime(2026, 11, 2)).date() == dt.date(2026, 11, 30)       # mês curto: último dia
    assert proxima({"freq": "horaria", "intervalo": 2}, AGORA) == dt.datetime(2026, 11, 2, 12, 0)
    assert proxima({"freq": "anual", "dia": 29, "mes": 2}, AGORA).date() == dt.date(2027, 2, 28)
    assert proxima(None) is None
    assert descrever_recorrencia({"freq": "mensal", "dia": 20, "hora": "09:00"}) == "todo dia 20 às 09:00"
    assert descrever_recorrencia({"freq": "semanal", "dias_semana": [0, 2, 4]}) == "toda segunda, quarta e sexta"
    assert descrever_recorrencia({"freq": "mensal", "ordinal": 1, "dias_semana": [6]}) == "todo primeiro domingo do mês"
    assert recorrencia("todo dia 20") == {"freq": "mensal", "dia": 20}


def test_valores_e_compatibilidade():
    v = {t.texto: t.valor for t in reconhecer("custa 1,5 mil, ou R$ 2k, ou R$ 1.250,00 ou 30 reais") if t.campo == "valor"}
    assert v == {"1,5 mil": "1500", "R$ 2k": "2000", "R$ 1.250,00": "1250.00", "30 reais": "30"}
    t = reconhecer("liga pro (11) 98765-4321")
    assert any(x.campo == "telefone" and x.valor == "11987654321" for x in t)
    # API antiga continua
    from djorge.runtime.recognizers import atalho_envio, combinar, corpo_mensagem, nome_do_email
    assert nome_do_email("contato@nubank.com.br")["entidade"] == "Nubank"
    assert atalho_envio("#a@b.com &oi&")["corpo"] == "oi"
    assert corpo_mensagem("manda pra Ana dizendo que vou atrasar").valor == "Vou atrasar"
    assert combinar("x", [], reconhecer("amanhã")) and reconhecer("amanhã", dt.date(2026, 11, 2))[0].valor == "2026-11-03"


def test_data_humana():
    assert data_humana("2026-11-03") == "2026-11-03"
    assert data_humana("2026-11-03T14:00:00Z") == "2026-11-03T14:00"
    assert data_humana("2026-11-03 14:00") == "2026-11-03T14:00"
    assert data_humana("03/11/2026 14h", AGORA) == "2026-11-03T14:00"
    assert data_humana("amanhã 9h", AGORA) == "2026-11-03T09:00"
    assert data_humana("3 de novembro às 9h", AGORA) == "2026-11-03T09:00"
    assert data_humana("31/02/2026") is None and data_humana("banana") is None and data_humana("2026-13-45") is None


# ------------------------------------------------------------------ pistas e decidir sem modelo --
CASOS = [
    ("marca dentista dia 20 às 9h", "cards"),
    ("me lembra de pagar o aluguel todo dia 20", "cards"),
    ("me lembra de tomar remédio dias úteis às 8h", "cards"),
    ("reunião de equipe toda primeira segunda do mês", "cards"),
    ("consulta 15/11 às 3 da tarde", "cards"),
    ("aniversário do João dia 15 de dezembro", "cards"),
    ("anota ideia de app de receitas", "cards"),
    ("não me deixa esquecer de buscar as crianças às 17h", "cards"),
    ("adiciona leite, ovos e café na lista de compras", "listas"),
    ("cria uma lista de viagem com protetor e chinelo", "listas"),
    ("manda pra Ana dizendo que vou atrasar", "bilhete"),
    ("envia um e-mail para edson@x.com avisando que chego às 10h", "bilhete"),
    ("#ana@x.com &chego às 10h&", "bilhete"),
    ("quanto é 18% de 2450?", "calcular"),
    ("2+2*3", "calcular"),
    ("quanto fica 1250 dividido por 3", "calcular"),
    ("que horas são?", "datas_horas"),
    ("que dia cai 25/12?", "datas_horas"),
    ("quantos dias faltam para 25/12?", "datas_horas"),
    ("o que tenho amanhã?", "proximas_tarefas"),
    ("como está minha semana?", "proximas_tarefas"),
    ("quais são os meus próximos compromissos", "proximas_tarefas"),
    ("procura o boleto da escola", "buscar"),
    ("onde guardei o contrato do aluguel", "buscar"),
    ("pesquisa na internet previsão do tempo", "web"),
    ("busca no google horário da farmácia", "web"),
    ("abre o whatsapp da Ana", "abrir"),
    ("abre o youtube", "abrir"),
    ("sempre que chegar boleto, cria uma tarefa", "regra"),
    ("me conta uma piada", "nenhuma"),
    ("cachorro", "nenhuma"),
]


@pytest.mark.parametrize("texto,tool", CASOS)
def test_pistas_decidem_sem_modelo(texto, tool):
    p = P.pistas(texto, reconhecer(texto, agora=AGORA))
    d = P.decidir(None, p, texto=texto)
    assert d["tool"] == tool, (texto, d, p["ferramentas"])
    assert set(d) >= {"tool", "confianca", "motivo", "alternativas_uteis", "perguntar", "decidido"}


def test_decidir_guardrails_com_modelo():
    texto = "marca dentista dia 20 às 9h"
    p = P.pistas(texto)
    # o modelo erra (bilhete) com pouca certeza e sem pista nenhuma: as pistas ganham
    assert P.decidir({"bilhete": 0.6, "cards": 0.3}, p, texto=texto)["tool"] == "cards"
    # modelo e pistas concordam: confiança alta
    d = P.decidir({"cards": 0.9, "bilhete": 0.05}, p, texto=texto)
    assert d["tool"] == "cards" and d["confianca"] > 0.8 and d["motivo"] == "modelo e pistas concordam"
    # regra explícita ganha do modelo
    p2 = P.pistas("#ana@x.com &oi&")
    assert P.decidir({"cards": 0.95}, p2)["tool"] == "bilhete"
    # calcular sem número é incompatível
    p3 = P.pistas("calcula pra mim")
    assert P.decidir({"calcular": 0.7}, p3, texto="calcula pra mim")["tool"] != "calcular"
    # o modelo diz "nenhuma" e não há pistas fortes
    assert P.decidir({"nenhuma": 0.8}, P.pistas("me conta uma piada"))["tool"] == "nenhuma"
    # empate entre duas ferramentas com pistas: pergunta com opções
    pe = {"ferramentas": {"cards": 0.6, "bilhete": 0.58}, "sinais": [{"tool": "bilhete", "sinal": "verbo de envio"}],
          "faltando": [], "explicito": None}
    d = P.decidir(None, pe)
    assert not d["decidido"] and d["perguntar"][0]["opcoes"][0]["patch"] == {"tool": "cards"}
    assert {o["rotulo"] for o in d["perguntar"][0]["opcoes"]} == {"Criar um card", "Mandar mensagem"}
    # aceita a lista [{tool,p}] do modelo
    assert P.decidir([{"tool": "cards", "p": 0.9}], p)["tool"] == "cards"


def test_pistas_faltando_e_contexto():
    p = P.pistas("manda uma mensagem")
    assert "destino" in p["faltando"] and "texto" in p["faltando"]
    assert P.pistas("cachorro", contexto={"tool": "web"})["explicito"] == "web"


def test_dicas():
    d = P.dicas("cachorro")
    assert 2 <= len(d) <= 3 and all(x.startswith("Tente: “") for x in d)
    assert any("cachorro" in x for x in d)
    d = P.dicas("dentista", tool="cards")
    assert any("dentista amanhã às 9h" in x for x in d)
    assert any("manda pra Ana" in x for x in P.dicas("manda uma mensagem"))


# ------------------------------------------------------------------ formatação --
def test_formatar():
    assert F.nome("ana maria da silva") == "Ana Maria da Silva"
    assert F.nome("JOÃO DOS SANTOS e souza") == "João dos Santos e Souza"
    assert F.nome("maria d'ávila") == "Maria d'Ávila"
    assert F.nome("banco xp") == "Banco XP"
    hoje = dt.date(2026, 11, 2)
    assert F.data("2026-11-03T14:00", hoje) == "amanhã às 14:00"
    assert F.data("2026-11-05T14:00", hoje) == "qui., 5 de nov. às 14:00"
    assert F.data("2026-11-02", hoje) == "hoje"
    assert F.data("2027-01-10", hoje) == "dom., 10 de jan. de 2027"
    assert F.data("2026-11-03T14:00", hoje, relativo=False) == "ter., 3 de nov. às 14:00"
    assert F.intervalo("2026-11-05T14:00", "2026-11-05T16:00", hoje) == "qui., 5 de nov. das 14:00 às 16:00"
    assert F.hora("9h") == "09:00" and F.hora("14:5") == "14:05" and F.hora("14h30") == "14:30"
    assert F.valor(1250) == "R$ 1.250,00" and F.valor("1250.5") == "R$ 1.250,50"
    assert F.valor("1.250,00") == "R$ 1.250,00" and F.valor("2k") == "R$ 2.000,00" and F.valor("1500") == "R$ 1.500,00"
    assert F.titulo("de pagar a conta de luz às") == "Pagar a conta de luz"
    assert F.titulo("reunião com ana souza", ["ana souza"]) == "Reunião com Ana Souza"
    assert F.frase("criei o card.") == "Criei o card." and F.frase("25/12 cai") == "25/12 cai"
    c = F.card({"title": "falar com joão", "people": [{"nome": "joão silva"}], "start": "2026-11-03T09:00"}, hoje)
    assert c["title"] == "Falar com joão" and c["people"][0]["nome"] == "João Silva" and c["quando_fmt"] == "amanhã às 09:00"
    out = F.resposta({"reply": "oi", "fields": [{"campo": "valor", "valor": "1500", "texto": "1,5 mil"},
                                                 {"campo": "data", "valor": "2026-11-03", "texto": "amanhã"}]}, hoje)
    assert out["reply"] == "Oi" and [f["valor_fmt"] for f in out["fields"]] == ["R$ 1.500,00", "amanhã"]


# ------------------------------------------------------------------ cards com datas humanas --
def test_cards_datas_humanas(app):
    C = app.cards
    a = C.create({"title": "A", "due": "2026-11-03 14:00"})
    assert a["due"] == "2026-11-03T14:00"
    b = C.create({"title": "B", "start": "03/11/2026 14h"})
    assert b["start"] == "2026-11-03T14:00"
    c = C.create({"title": "C", "due": "amanhã 9h"})
    assert c["due"] == (dt.date.today() + dt.timedelta(1)).isoformat() + "T09:00"
    from djorge_local.cards import DataInvalida
    with pytest.raises(DataInvalida) as e:
        C.create({"title": "D", "due": "31/02/2026"})
    msg = str(e.value)
    assert "Não entendi a data" in msg and "prazo" in msg and "Tente" in msg and "ISO 8601" not in msg
    assert isinstance(e.value, ValueError) and e.value.dica
    assert C.update(a["id"], {"due": "2026-12-01"})["due"] == "2026-12-01"


# ------------------------------------------------------------------ web --
DDG_JSON = json.dumps({"Heading": "Python", "AbstractText": "Python é uma linguagem.", "AbstractURL": "https://pt.wikipedia.org/wiki/Python",
                       "RelatedTopics": [{"Text": "Python Software Foundation - organização", "FirstURL": "https://www.python.org/psf"},
                                         {"Topics": [{"Text": "Monty Python - grupo", "FirstURL": "https://duckduckgo.com/Monty_Python"}]}]})
DDG_HTML = """<div class="result"><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexemplo.com.br%2Fpagina&rut=x">Exemplo &amp; Cia</a>
<a class="result__snippet" href="#">Um <b>trecho</b> bom</a></div>
<div class="result"><a class="result__a" href="https://outro.org/x">Outro</a><div class="result__snippet">Mais texto</div></div>"""


def test_web_api_e_fallback():
    urls = []

    def falso(url, timeout):
        urls.append((url, timeout))
        return DDG_JSON.encode() if "api.duckduckgo" in url else DDG_HTML
    r = web.buscar_web("python linguagem", n=6, transporte=falso)
    assert r[0] == {"titulo": "Python", "url": "https://pt.wikipedia.org/wiki/Python", "trecho": "Python é uma linguagem.",
                    "fonte": "pt.wikipedia.org"}
    assert {x["fonte"] for x in r} >= {"python.org", "duckduckgo.com"}
    assert all(t == 8.0 for _, t in urls) and "q=python+linguagem" in urls[0][0]

    def so_html(url, timeout):
        if "api." in url:
            return b'{"AbstractText": "", "RelatedTopics": []}'
        return DDG_HTML
    r = web.buscar("algo raro", transporte=so_html)
    assert [x["url"] for x in r["resultados"]] == ["https://exemplo.com.br/pagina", "https://outro.org/x"]
    assert r["resultados"][0]["titulo"] == "Exemplo & Cia" and r["resultados"][0]["trecho"] == "Um trecho bom"
    assert r["sai_do_computador"] is True and r["erro"] is None
    r = web.buscar("x", transporte=_offline)
    assert r["resultados"] == [] and "internet" in r["erro"]
    assert web.buscar("", transporte=_offline)["resultados"] == []


def test_comando_web_e_busca(app):
    app.command.web_transporte = lambda url, t: DDG_JSON
    r = app.command.run("pesquisa na internet linguagem python")
    # rodada 3: pergunta antes de sair do computador; a busca roda no confirm
    assert r["tool"] == "web" and r["proposal"] == {"action": "web_search", "q": "linguagem python"}
    assert r["needs_confirm"] and r["results"] == [] and r["sai_do_computador"] and "na internet" in r["reply"]
    res = app.command.confirm(r["id"], True)
    assert res["results"][0]["source"] == "web" and res["sai_do_computador"] and "linguagem python" in res["reply"]
    # busca local: só vai à internet se não houver nada local
    app.search.web_transporte = lambda url, t: DDG_JSON
    app.cards.create({"title": "Boleto da escola"})
    assert {x["source"] for x in app.search.search("boleto", web="auto")["results"]} == {"cards"}
    r = app.search.search("zzqx", web="auto")
    assert r.get("web") and r["results"][0]["source"] == "web"
    assert app.search.search("zzqx")["results"] == []                  # padrão: nada sai do computador
    assert app.search.search("python", "web")["results"][0]["fonte"] == "pt.wikipedia.org"


# ------------------------------------------------------------------ comando --
def test_comando_cards_regras(app):
    K = app.command
    r = K.run("me lembra de pagar o aluguel todo dia 20 às 9h")
    c = r["results"][0]
    assert r["tool"] == "cards" and c["kind"] == "lembrete" and c["title"] == "Pagar o aluguel"
    assert c["start"].endswith("-20T09:00") and c["recurrence"] == "todo dia 20 às 09:00"
    assert r["proposal"]["recorrencia"] == {"freq": "mensal", "dia": 20, "hora": "09:00"}
    assert "tools" not in r and r["confianca"] == r["tool_p"]
    assert r["authorization"]["decisao"] == "seguir" and not r["needs_confirm"]
    assert all("valor_fmt" in f for f in r["fields"]) and r["titulo"] == "Pagar o aluguel"
    r = K.run("reunião com a equipe das 14h às 16h amanhã")
    c = r["results"][0]
    assert c["kind"] == "evento" and c["start"].endswith("T14:00") and c["end"].endswith("T16:00")
    assert "das 14:00 às 16:00" in r["reply"]
    r = K.run("academia toda segunda e quarta às 7h")
    assert r["results"][0]["recurrence"] == "toda segunda e quarta às 07:00"
    r = K.run("marca reunião")
    assert r["executed"] and r["dicas"]                                      # sem data: card + dica de como pôr data


def test_comando_dicas_e_perguntas(app):
    r = app.command.run("cachorro")
    assert r["tool"] == "nenhuma" and r["proposal"]["action"] == "none"
    assert r["dicas"] and r["dicas"][0].startswith("Tente: “") and not app.cards.list()
    r = app.command.run("cachorro", context={"tool": "web"})
    assert r["tool"] == "web"
    r = app.command.run("manda uma mensagem")
    assert r["tool"] == "bilhete" and r["dicas"] and "Para quem" in r["reply"]


def test_comando_abrir_whatsapp(app):
    app.cards.create({"title": "Almoço", "people": [{"nome": "Ana Souza", "email": "ana@x.com"}]})
    r = app.command.run('abre o whatsapp da Ana com "tô chegando"')
    p = r["proposal"]
    assert r["tool"] == "abrir" and p["action"] == "abrir_app" and p["alvo"] == "whatsapp" and r["needs_confirm"]
    assert p["texto"] == "tô chegando" and p["url"].startswith("https://wa.me/") and "t%C3%B4%20chegando" in p["url"]
    assert p["contato"]["nome"] == "Ana Souza" and p["contato"]["email"] == "ana@x.com"
    res = app.command.confirm(r["id"], True)
    assert res["ok"] and res["abrir"]["url"] == p["url"]
    r = app.command.run("abre o site gov.br")
    assert r["proposal"]["url"] == "https://gov.br" and r["proposal"]["alvo"] == "navegador"
    # contato de T (djorge_local.contatos ou app.contatos) com telefone
    class Contatos:
        def buscar(self, q):
            return [{"nome": "ana souza", "telefone": "(11) 98765-4321", "email": None}]
    app.contatos = Contatos()
    r = app.command.run("abre o whatsapp da Ana dizendo que chego às 10h")
    assert r["proposal"]["url"].startswith("https://wa.me/5511987654321?text=Chego")
    assert r["proposal"]["app_url"].startswith("whatsapp://send?phone=5511987654321")


def test_etapas_stream(app):
    K = app.command
    r = K.run("me lembra de ligar pro João amanhã às 10h", stream=True)
    assert set(r) == {"id", "stream"}
    for _ in range(200):
        e = K.etapas(r["id"])
        if e["pronto"]:
            break
        time.sleep(0.02)
    textos = [x["texto"] for x in e["etapas"]]
    assert textos[0] == "Lendo o seu pedido…" and "Djorge está entendendo o que você quer…" in textos
    assert any(t.startswith("Reconheceu a data ") for t in textos) and "Djorge está usando a ferramenta Cards…" in textos
    assert textos[-1] == "Pronto" and all(x["estado"] == "ok" for x in e["etapas"])
    assert e["resultado"]["tool"] == "cards" and e["resultado"]["executed"]
    s = K.run("2+2")                                                     # sem stream também registra
    assert K.etapas(s["id"])["pronto"] and "Djorge está usando a ferramenta Calcular…" in [x["texto"] for x in K.etapas(s["id"])["etapas"]]
    r = K.run("mova e-mails da Ana para Trabalho automaticamente", stream=True)   # regra de árvore sem modelo: erro amigável
    for _ in range(200):
        e = K.etapas(r["id"])
        if e["pronto"]:
            break
        time.sleep(0.02)
    assert e["resultado"]["error"] == "modelo_ausente" and e["etapas"][-1]["estado"] == "erro"
    with pytest.raises(KeyError):
        K.etapas("nao-existe")
    c = K.run("pesquisa na internet clima", stream=False)
    assert "Entendeu: buscar na internet" in [x["texto"] for x in K.etapas(c["id"])["etapas"]]


class _Integ:
    def __init__(self):
        self.enviados = []

    def list(self):
        return [{"id": "google", "name": "Google", "connected": True}]

    def send(self, cid, to, body, subject=None, **kw):
        self.enviados.append((cid, to, body))
        return {"ok": True, "id": "m1"}


def test_confirm_troca_tipo(app):
    K = app.command
    # mensagem -> tarefa
    r = K.run("manda pra Ana dizendo que vou atrasar")
    res = K.confirm(r["id"], True, {"action": "create_card", "card": {"kind": "tarefa"}, "data": "amanhã às 9h"})
    c = res["card"]
    assert c["kind"] == "tarefa" and c["due"].endswith("T09:00") and app.cards.get(c["id"])
    assert "Vou atrasar" in c["title"] and c["people"][0]["nome"] == "Ana" and res["reply"].startswith("Criei a tarefa")
    # card criado sozinho -> vira evento com título e data novos (no mesmo card)
    r = K.run("me lembra de pagar a luz amanhã às 15h")
    cid = r["results"][0]["id"]
    res = K.confirm(r["id"], True, {"kind": "evento", "title": "pagar a conta de luz da casa", "data": "15/11 às 3 da tarde"})
    c = app.cards.get(cid)
    assert c["kind"] == "evento" and c["title"] == "Pagar a conta de luz da casa" and c["start"].endswith("-11-15T15:00")
    assert len(app.cards.list()) == 2 and res["reply"].startswith("Atualizei o evento")
    # card -> mensagem (com integração falsa conectada): envia e apaga o card criado sozinho
    integ = _Integ()
    app._svc["integrations"] = integ
    if not isinstance(getattr(type(app), "integrations", None), property):
        app.integrations = integ
    r = K.run("me lembra de ligar pro Edson amanhã às 10h")
    cid = r["results"][0]["id"]
    res = K.confirm(r["id"], True, {"action": "send_message", "to": "edson@x.com", "body": "posso te ligar amanhã?"})
    assert res["ok"] and integ.enviados == [("google", "edson@x.com", "Posso te ligar amanhã?")]
    assert app.cards.get(cid) is None and res["proposal"]["action"] == "send_message"
    # data impossível na edição: erro amigável
    r = K.run("me lembra de regar as plantas amanhã às 8h")
    with pytest.raises(ValueError) as e:
        K.confirm(r["id"], True, {"data": "banana"})
    assert "não entendi a data" in str(e.value)
