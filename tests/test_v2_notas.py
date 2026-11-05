"""Rodada 4: bloco de notas (CRUD, autosave, versões, busca, analisar, perguntar, tarefas -> cards)."""
import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from test_v2_secretario import app, srv  # noqa: F401,E402

NOTA = """# Reunião da obra

Conversamos com o João Silva sobre a cozinha. O orçamento ficou em R$ 1.250,00.
- [ ] Ligar para o pedreiro amanhã às 10h
- [x] Comprar tinta
Preciso enviar o contrato até sexta.
A bancada será de granito cinza.
"""


def test_crud_autosave_versoes(app, srv):
    code, n = srv("/api/notas", {"titulo": "Obra", "texto": NOTA, "pasta": "Casa", "etiquetas": "obra, #casa"})
    assert code == 200 and n["corpo"] == NOTA and n["etiquetas"] == ["obra", "casa"]
    code, n2 = srv(f"/api/notas/{n['id']}", {"corpo": NOTA + "\nmais", "versao": True})
    assert n2["corpo"].endswith("mais") and n2["titulo"] == "Obra"
    srv(f"/api/notas/{n['id']}", {"corpo": NOTA + "\nmais ainda"})          # autosave: sem nova versão já
    code, v = srv(f"/api/notas/{n['id']}/versoes")
    assert len(v["versoes"]) == 1 and v["versoes"][0]["corpo"] == NOTA
    code, n3 = srv(f"/api/notas/{n['id']}/versoes", {"restaurar": v["versoes"][0]["id"]})
    assert n3["corpo"] == NOTA
    srv("/api/notas", {"titulo": "Mercado", "corpo": "leite e pão", "fixada": True})
    code, l = srv("/api/notas")
    assert l["notas"][0]["titulo"] == "Mercado" and l["pastas"] == ["Casa"] and "obra" in l["etiquetas"]
    code, l = srv("/api/notas?q=granito")
    assert [x["titulo"] for x in l["notas"]] == ["Obra"]
    code, r = srv(f"/api/notas/{n['id']}/delete", {})
    assert r["ok"] and srv(f"/api/notas/{n['id']}")[0] == 404


def test_analisar_regras():
    from djorge_local.notas import analisar
    r = analisar({"titulo": "", "corpo": NOTA, "etiquetas": []}, None, ["obra", "viagem"],
                 dt.datetime(2026, 11, 2, 12, 0))
    assert r["fonte"] == "regras" and r["titulo_sugerido"] == "Reunião da obra"
    frases = [t["frase"] for t in r["tarefas"]]
    assert any("pedreiro" in f for f in frases) and any("contrato" in f for f in frases)
    assert not any("tinta" in f for f in frases)                         # já feita
    ped = next(t for t in r["tarefas"] if "pedreiro" in t["frase"])
    assert ped["card"]["due"].startswith("2026-11-03T10:00")
    assert "João Silva" in r["pessoas"] and r["etiquetas"] == ["obra"]
    assert r["datas"] and len(r["resumo"]) >= 1
    for s in r["resumo"]:
        assert s in NOTA.replace("- [ ] ", "").replace("# ", "")       # extrativo


class FakeEng:
    def run(self, reqs):
        out = []
        for q in reqs:
            if q.kind == "NOUL":
                out.append({"p_true": 0.9 if "granito" in q.source else 0.1})
            elif q.kind == "CHOICE":
                out.append({"distribution": {o: (0.8 if o == "viagem" else 0.2 / len(q.options)) for o in q.options}})
            else:
                class M:
                    start, end, probability, attributes = q.source.index("João"), q.source.index("João") + 4, .9, {}
                class MS:
                    matches = [M()]
                out.append(MS())
        return out


def test_analisar_com_modelo_em_lote():
    from djorge_local.notas import analisar
    r = analisar({"titulo": "", "corpo": NOTA, "etiquetas": []}, FakeEng(), ["obra", "viagem"])
    assert r["fonte"] == "modelo" and r["etiquetas"][0] == "viagem"
    assert any("granito" in t["frase"] for t in r["tarefas"]) and r["pessoas"][0] == "João"


def test_perguntar_e_tarefas(app, srv):
    code, n = srv("/api/notas", {"titulo": "Obra", "corpo": NOTA})
    code, r = srv(f"/api/notas/{n['id']}/perguntar", {"q": "qual o material da bancada?"})
    assert "granito" in r["trechos"][0]["texto"]
    code, a = srv(f"/api/notas/{n['id']}/analisar", {})
    idx = [t["indice"] for t in a["tarefas"]]
    code, r = srv(f"/api/notas/{n['id']}/tarefas", {"indices": idx[:1]})
    assert code == 200 and r["criados"] == 1 and app.cards.get(r["cards"][0]["id"])
    assert srv(f"/api/notas/{n['id']}")[1]["cards"] == [r["cards"][0]["id"]]
    assert app.hist.last()[0]["ferramenta"] == "notas"
    assert srv(f"/api/notas/{n['id']}/tarefas", {"indices": [999]})[0] == 400
