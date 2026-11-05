from djorge_local import floreios as fl


def test_tom_regras():
    assert fl.tom("Aniversário da Ana") == "feliz"
    assert fl.tom("Boleto vence hoje — multa de 2%") == "serio"
    assert fl.tom("Comprar pão") == "neutro"
    assert fl.tom("qualquer", categoria="saude") == "serio"


def test_tom_engine():
    class Eng:
        def run(self, reqs):
            assert reqs[0].kind == "CHOICE" and reqs[0].options == fl.CHOICE_OPCOES
            return [{"answer": fl.CHOICE_OPCOES[0]}]
    assert fl.tom("Ligar para o João", engine=Eng()) == "feliz"


def test_enfeitar():
    a = fl.enfeitar("Projeto entregue", "Você concluiu tudo.", "feliz", seed=1)
    assert a["titulo"] != "Projeto entregue" and "Projeto entregue" in a["titulo"] and a["icone"]
    assert fl.enfeitar("Projeto entregue", "x", "feliz", seed=1) == fl.enfeitar("Projeto entregue", "x", "feliz", seed=1)
    s = fl.enfeitar("Boleto vence hoje", "Pague até 18h.", "serio", seed=2)
    assert s["titulo"].split(":")[0] in ("Atenção", "Importante") and s["mensagem"] == "Pague até 18h."
    assert "Uhu" not in s["titulo"] and "🎉" not in s["titulo"]
    assert fl.enfeitar("Atenção: prazo", "m", "serio")["titulo"] == "Atenção: prazo"
    assert fl.enfeitar("Comprar pão", "m", "neutro") == {"titulo": "Comprar pão", "mensagem": "m", "icone": "🔔"}
    an = fl.enfeitar("Aniversário de Ana", "", "feliz", ocasiao={"tipo": "aniversario", "pessoa": "Ana"}, seed=3)
    assert "Ana" in an["mensagem"] and "parabéns" in an["mensagem"].lower() and an["icone"] == "🎂"
    fe = fl.enfeitar("Finados", "planeje a semana.", "feliz",
                     ocasiao={"tipo": "feriado", "nome": "Finados", "quando": "amanhã"}, seed=4)
    assert "Finados" in fe["mensagem"] and "feriado" in fe["mensagem"].lower()
    off = fl.enfeitar("Projeto entregue", "x", "feliz", ativo=False)
    assert off["titulo"] == "Projeto entregue" and off["icone"] == ""
    variados = {fl.enfeitar("Meta batida", "", "feliz", seed=i)["titulo"] for i in range(30)}
    assert len(variados) >= 2
