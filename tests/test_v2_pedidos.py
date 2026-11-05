"""Pedidos (V2_SPEC_4): extração {titulo, tarefa, texto_central, quem, prazo, valor} por regras e pelo modelo (lote),
card do pedido no secretário, remetente certo (from/sender; app não vira pessoa) e conector de notificações."""
import datetime as dt
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from djorge.runtime.triagem import ASK_FRASE, ASK_PEDIDO, extrair_pedido, triar

H = dt.date(2026, 11, 5)


def test_regras_extraem_pedido():
    x = triar("Oi Ana, você pode revisar a proposta até sexta? É urgente", remetente="marina@agencia.com.br", hoje=H)
    p = x["pedido"]
    assert p["tarefa"] == "Revisar a proposta" and p["titulo"] == "Revisar a proposta"
    assert p["texto_central"] == "Você pode revisar a proposta até sexta?"
    assert p["quem"] == "Marina" and p["prazo"] == "2026-11-06" and p["fonte"] == "regras"
    assert "criar tarefa" in x["acoes"]
    p = triar("Bom dia! Tudo bem? Por favor, envie o relatório de vendas de outubro amanhã às 10h.", hoje=H)["pedido"]
    assert p["tarefa"] == "Enviar o relatório de vendas de outubro"
    assert p["titulo"] == "Enviar o relatório de vendas…"
    p = triar("Preciso que você pague o boleto de R$ 120,00 hoje.", remetente="Carlos", hoje=H)["pedido"]
    assert p["tarefa"].startswith("Pagar o boleto") and p["valor"] and p["quem"] == "Carlos"


def test_sem_pedido():
    assert triar("Saudade de você, beijo", hoje=H)["pedido"] is None
    assert triar("Só hoje! 50% de desconto. Cupom BLACK50", remetente="news@loja.com.br", hoje=H)["pedido"] is None
    assert triar("Sua fatura de R$ 230,45 vence amanhã.", remetente="cobranca@enel.com.br", hoje=H)["pedido"] is None


class FakeEngine:
    def __init__(self, p_pedido=0.9, frase=None, pessoa=None):
        self.p, self.frase, self.pessoa, self.chamadas = p_pedido, frase, pessoa, []

    def run(self, reqs):
        self.chamadas.append(reqs)
        out = []
        for r in reqs:
            if r.kind == "NOUL":
                assert r.options == [ASK_PEDIDO]
                out.append({"p_true": self.p})
            elif r.kind == "SCORE":
                out.append({"distribution": {o: 0.25 for o in r.options}})
            elif r.kind == "CHOICE" and r.ask == ASK_FRASE:
                f = self.frase or r.options[0]
                out.append({"answer": f, "probability": 0.8})
            elif r.kind == "CHOICE":
                d = {o: 1 / len(r.options) for o in r.options}
                if "seguro" in d:
                    d = {o: (0.96 if o == "seguro" else 0.01) for o in r.options}
                out.append({"distribution": d, "answer": max(d, key=d.get), "probability": max(d.values())})
            elif r.kind == "resolve":
                assert r.mode == "ENTITY" and r.target == "pessoa"
                ms = []
                if self.pessoa and self.pessoa in r.source:
                    i = r.source.index(self.pessoa)
                    ms.append(SimpleNamespace(start=i, end=i + len(self.pessoa), probability=0.9))
                out.append(SimpleNamespace(matches=ms))
            else:
                raise AssertionError(r.kind)
        return out


def test_modelo_em_lote_decide_pedido():
    txt = "Oi! Ontem foi ótimo. A Júlia pediu: traga os documentos do carro na segunda. Abraço"
    eng = FakeEngine(frase="A Júlia pediu: traga os documentos do carro na segunda.", pessoa="Júlia")
    x = triar(txt, remetente="Pedro", engine=eng, hoje=H)
    assert len(eng.chamadas) == 1                              # todas as perguntas num só engine.run
    assert {r.kind for r in eng.chamadas[0]} == {"CHOICE", "SCORE", "NOUL", "resolve"}
    p = x["pedido"]
    assert p["fonte"] == "modelo" and p["quem"] == "Júlia" and p["p"] == 0.9
    assert p["tarefa"] == "Trazer os documentos do carro"
    assert p["texto_central"].startswith("A Júlia pediu")
    # o modelo diz que não é pedido: as regras não forçam
    assert triar("Você pode revisar a proposta?", engine=FakeEngine(p_pedido=0.1), hoje=H)["pedido"] is None
    # dúvida do modelo + pista forte das regras = pedido
    assert triar("Você pode revisar a proposta?", engine=FakeEngine(p_pedido=0.4), hoje=H)["pedido"]


def test_extrair_pedido_direto():
    p = extrair_pedido("Favor assinar o contrato até amanhã.", remetente="RH")
    assert p["tarefa"] == "Assinar o contrato" and p["quem"] == "RH"
    assert extrair_pedido("Chegou sua encomenda.") is None


# --------------------------------------------------------------------------- secretário --
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
    a.secretario.agora = lambda: dt.datetime(2026, 11, 5, 12, 0)
    yield a
    a.cards.close()
    a.secretario.close()


def test_card_do_pedido(app):
    sec = app.secretario
    sec.configurar({"ativo": True, "modo": "agir"})
    r = sec.processar({"source": "email", "title": "Proposta", "from": "Marina Souza <marina@agencia.com.br>",
                       "text": "Oi! Você pode revisar a proposta até amanhã? Obrigada"})
    c = app.cards.get(r["card_id"])
    assert c["title"] == "Revisar a proposta"
    assert c["body"].startswith("Você pode revisar a proposta até amanhã?")
    assert "Pedido por: Marina" in c["body"] and c["due"] == "2026-11-06"
    n = sec.store.get(r["notificacao"])
    assert n["remetente"].startswith("Marina") and n["dados"]["pedido"]["tarefa"] == "Revisar a proposta"


def test_desligado_so_lista_e_etiqueta(app):
    sec = app.secretario
    usados = []
    app.command.engine = lambda: usados.append(1) or None
    r = sec.processar({"source": "notifications", "app": "Nubank", "title": "Fatura",
                       "sender": "Fatura", "text": "Sua fatura de R$ 230,45 vence amanhã"})
    n = sec.store.get(r["notificacao"])
    assert n["etiqueta"] == "financeiro" and not n["acoes"] and not n["sugestoes"] and not usados
    assert n["remetente"] != "Fatura"                         # app não vira pessoa
    r = sec.processar({"source": "notifications", "app": "WhatsApp", "title": "Ana Lima", "text": "Oi, tudo bem?"})
    assert sec.store.get(r["notificacao"])["remetente"] == "Ana Lima"


def test_remetente_de():
    from djorge_local.secretario import remetente_de
    assert remetente_de({"from": "joao@x.com"}) == "joao@x.com"
    assert remetente_de({"autor": "Bia"}) == "Bia"
    assert remetente_de({"source": "notifications", "app": "Itaú", "title": "Pix recebido"}) == "Itaú"
    assert remetente_de({"source": "notifications", "app": "Telegram", "title": "Rui"}) == "Rui"


def test_conector_notificacoes_ligado_por_padrao(monkeypatch):
    import djorge_local.connectors as C
    monkeypatch.setattr(C.platform, "system", lambda: "Windows")
    assert C.ligado("notifications", {"enabled": True}) and C.ligado("notifications", {})
    assert not C.ligado("notifications", {"enabled": False}) and not C.ligado("folder", {})
    n = C.Notifications({}, {"apps_deny": ["Spotify"]})
    assert not n._ok_app("Spotify") and n._ok_app("Outlook")
    KB = SimpleNamespace(toast_generic="g")
    textos = [SimpleNamespace(text="Ana"), SimpleNamespace(text="Chega às 8?")]
    nt = SimpleNamespace(app_info=SimpleNamespace(display_info=SimpleNamespace(display_name="WhatsApp")),
                         notification=SimpleNamespace(visual=SimpleNamespace(
                             get_binding=lambda k: SimpleNamespace(get_text_elements=lambda: textos))))
    assert C.Notifications._ler_win(nt, KB) == ("WhatsApp", ["Ana", "Chega às 8?"])
