import datetime as dt

from djorge_local import calendario as cal, contexto_usuario as cu


def _nomes(ano, uf=None):
    return {e["nome"]: e for e in cal.datas(ano, uf)}


def test_moveis_2026():
    assert cal.pascoa(2026) == dt.date(2026, 4, 5)
    n = _nomes(2026)
    assert n["Carnaval (segunda-feira)"]["data"] == "2026-02-16"
    assert n["Carnaval (terça-feira)"]["data"] == "2026-02-17"
    assert n["Quarta-feira de Cinzas (até 14h)"]["data"] == "2026-02-18"
    assert n["Sexta-feira Santa"]["data"] == "2026-04-03" and n["Sexta-feira Santa"]["tipo"] == "feriado"
    assert n["Corpus Christi"]["data"] == "2026-06-04"
    assert n["Dia das Mães"]["data"] == "2026-05-10"
    assert n["Dia dos Pais"]["data"] == "2026-08-09"
    assert n["Black Friday"]["data"] == "2026-11-27"
    assert n["Finados"]["tipo"] == "feriado" and n["Finados"]["nacional"]
    assert cal.pascoa(2025) == dt.date(2025, 4, 20) and cal.pascoa(2024) == dt.date(2024, 3, 31)
    assert cal.HORARIO_DE_VERAO["ativo"] is False


def test_estaduais_e_proximas():
    assert "Revolução Constitucionalista" in _nomes(2026, "SP")
    assert "Revolução Constitucionalista" not in _nomes(2026)
    assert _nomes(2026, "rj")["Dia de São Jorge"]["data"] == "2026-04-23"
    px = cal.proximas(dt.datetime(2026, 11, 5, 9), dias=30)
    nomes = [e["nome"] for e in px]
    assert "Proclamação da República" in nomes and "Black Friday" in nomes and "Finados" not in nomes
    assert px[0]["dias"] >= 0


def test_boleto_em_feriado_sugere_dia_util_anterior():
    c = {"title": "Pagar boleto do condomínio", "due": "2026-11-02"}  # Finados (segunda)
    out = cal.conflitos(c, "")
    assert out and out[0]["sugestao"] == "2026-10-30" and "Finados" in out[0]["motivo"]
    c2 = {"title": "Pagar fatura", "due": "2026-02-17"}  # Carnaval
    assert cal.conflitos(c2)[0]["sugestao"] == "2026-02-13"
    assert cal.conflitos({"title": "Pagar boleto", "due": "2026-11-04"}) == []


def test_conflitos_contexto_e_uf():
    c = {"title": "Reunião com cliente", "start": "2026-07-09T10:00"}
    assert cal.conflitos(c, "") == []
    assert cal.conflitos(c, "Moro em São Paulo e trabalho em banco")
    ponte = cal.conflitos({"title": "Entrega do relatório", "due": "2026-06-05"})  # sexta após Corpus Christi? (facult.)
    assert isinstance(ponte, list)
    v = cal.conflitos({"title": "Voo para Recife", "start": "2026-11-19T18:00"})  # véspera Consciência Negra
    assert any("véspera" in o["motivo"] for o in v)


def test_conflitos_engine_filtra():
    class Eng:
        def run(self, reqs):
            assert reqs[0].kind == "NOUL" and reqs[0].options == [cal.NOUL_CONFLITO]
            return [{"answer": False, "p_true": 0.05} for _ in reqs]
    c = {"title": "Reunião com cliente", "start": "2026-11-20T10:00"}
    assert cal.conflitos(c, "")
    assert cal.conflitos(c, "", engine=Eng()) == []


def test_aniversarios():
    cards = [{"id": "a", "title": "Aniversário da Ana", "start": "2025-11-05"},
             {"id": "b", "nome": "Bruno", "aniversario": "1990-11-07"},
             {"id": "c", "title": "x", "people": [{"nome": "Carla", "aniversario": "03-01"}]}]
    hoje = cal.aniversarios(cards, dt.datetime(2026, 11, 5, 8))
    assert [e["nome"] for e in hoje] == ["Ana"]
    sem = cal.aniversarios(cards, dt.date(2026, 11, 5), dias=7)
    assert {e["nome"] for e in sem} == {"Ana", "Bruno"}
    assert [e for e in sem if e["nome"] == "Bruno"][0]["idade"] == 36


def test_contexto_usuario():
    s = cu.sinais("Moro em São Paulo, trabalho em banco e tenho dois filhos. CPF 123.456.789-09")
    assert s["uf"] == "SP" and s["trabalho"] == "banco" and s["filhos"] is True
    n = cu.normalizar("meu cpf 123.456.789-09 cartão 4111 1111 1111 1111 " + "x" * 600)
    assert "123.456" not in n and "4111" not in n and len(n) <= 401
    assert cu.sinais("Sou de Curitiba")["uf"] == "PR"
    assert cu.para_modelo("") == "" and cu.para_modelo("moro no RJ").startswith("Sobre o usuário:")
