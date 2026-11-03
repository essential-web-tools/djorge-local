"""Rodada 3: tudo que a interface altera fica salvo no backend e volta igual quando o App é reaberto
(preferências, secretário, cards, automações, árvores editadas, API local, leveza, voz)."""
import json
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("DJORGE_LOCAL_HOME", str(tmp_path))
    monkeypatch.setenv("DJORGE_DATA_DIR", str(tmp_path))
    import yaml
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    (tmp_path / "arvores.yaml").write_text("trees: []\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({
        "device": "cpu", "models": {"flash": {"path": ""}}, "server": {"port": port},
        "seguranca": {"enabled": False}}), encoding="utf-8")
    return tmp_path


class Run:
    """Um App de verdade servindo HTTP; reabrir() fecha e cria outro na mesma pasta."""

    def __init__(self, home):
        self.home = home
        self._abrir()

    def _abrir(self):
        import importlib
        import djorge_local.config as C
        importlib.reload(C)
        from djorge_local.app import App, serve
        self.cfg = C.load(str(self.home / "config.yaml"))
        self.app = App(self.cfg)
        self.httpd = serve(self.app, block=False)
        self.base = f"http://127.0.0.1:{self.cfg['server']['port']}"
        self.tok = self.cfg["server"]["token"]

    def fechar(self):
        self.httpd.shutdown(); self.httpd.server_close()
        for x in (self.app.cards, getattr(self.app, "secretario", None)):
            try:
                x.close()
            except Exception:
                pass

    def reabrir(self):
        self.fechar(); self._abrir()

    def __call__(self, path, body=None):
        h = {"Content-Type": "application/json", "X-Token": self.tok}
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers=h)
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            raw = e.read()
            return e.code, (json.loads(raw) if raw[:1] == b"{" else raw)


@pytest.fixture()
def run(home):
    r = Run(home)
    yield r
    r.fechar()


def test_preferencias_e_perfil_persistem(run):
    patch = {"theme": "escuro", "density": "compacta", "accent": "azul", "font_size": "grande",
             "default_view": "kanban", "kanban_columns": ["entrada", "hoje", "feito"], "voice_enabled": True,
             "speak_replies": True, "nome_usuario": "Ana", "nome_agente": "Jorginho", "prioridade": "regras",
             "saved_filters": [{"name": "urgentes", "filters": {"priority": "3"}}]}
    c, r = run("/api/ui", patch)
    assert c == 200, r
    run.reabrir()
    c, r = run("/api/ui")
    p = r["prefs"]
    for k, v in patch.items():
        assert p[k] == v, k
    # nome do assistente vazio volta ao padrão
    run("/api/ui", {"nome_agente": ""})
    assert run("/api/ui")[1]["prefs"]["nome_agente"] == "Djorge"


def test_secretario_persiste(run):
    c, r = run("/api/secretario", {"ativo": True, "modo": "agir", "vips": ["Chefe"], "apps_ignorados": ["Steam"],
                                   "horario_silencio": {"ativo": True, "inicio": "22:00", "fim": "07:00"},
                                   "resumo_diario_hora": "08:30"})
    assert c == 200, r
    run.reabrir()
    s = run("/api/secretario")[1]
    assert s["ativo"] is True and s["modo"] == "agir"
    assert s["vips"] == ["Chefe"] and s["apps_ignorados"] == ["Steam"]
    assert s["horario_silencio"]["inicio"] == "22:00" and s["resumo_diario_hora"] == "08:30"
    run("/api/secretario", {"ativo": False})
    run.reabrir()
    assert run("/api/secretario")[1]["ativo"] is False


def test_cards_editar_mover_persistem(run):
    c, card = run("/api/cards", {"title": "Pagar luz", "kind": "tarefa"})
    assert c == 200, card
    cid = card["id"]
    c, r = run(f"/api/cards/{cid}", {"title": "Pagar a luz", "priority": 3, "tags": ["casa"],
                                     "checklist": [{"text": "ver valor", "done": True}], "color": "#4f7d96"})
    assert c == 200, r
    c, r = run("/api/cards/move", {"id": cid, "column": "fazendo", "order": 2})
    assert c == 200, r
    run.reabrir()
    x = run(f"/api/cards/{cid}")[1]
    assert x["title"] == "Pagar a luz" and x["priority"] == 3 and x["tags"] == ["casa"]
    assert x["column"] == "fazendo" and x["color"] == "#4f7d96"
    assert x["checklist"][0]["text"] == "ver valor"


def _auto():
    return {"nome": "Boletos", "ativa": True,
            "quando": {"fontes": ["notifications"], "palavras": ["boleto"], "apps": [], "remetente": "",
                       "assunto": ""},
            "entao": [{"acao": "tarefa", "params": {"titulo": "Pagar {remetente}", "campos": ["valor", "prazo"]}}]}


def test_automacao_salvar_editar_e_editor_visual(run):
    c, a = run("/api/automacoes", _auto())
    assert c == 200, a
    aid = a["id"]
    a["nome"] = "Boletos da casa"; a["quando"]["palavras"] = ["boleto", "fatura"]
    assert run("/api/automacoes", a)[0] == 200
    assert run(f"/api/automacoes/{aid}/desativar", {})[0] == 200
    run.reabrir()
    x = run(f"/api/automacoes/{aid}")[1]
    assert x["nome"] == "Boletos da casa" and x["quando"]["palavras"] == ["boleto", "fatura"] and x["ativa"] is False
    # editor visual: abre o grafo, muda o título da ação e salva de volta como automação
    c, r = run(f"/api/automacoes/{aid}/arvore")
    assert c == 200 and r["graph"]["nodes"], r
    g = r["graph"]
    acao = next(n for n in g["nodes"] if n["kind"] == "action")
    acao["props"]["titulo"] = "Conta: {remetente}"
    filtro = next(n for n in g["nodes"] if n["kind"] == "if")
    filtro["props"]["if"]["contains_any"] = ["boleto", "fatura", "conta"]
    c, r = run(f"/api/automacoes/{aid}/arvore", {"graph": g})
    assert c == 200 and r["modo"] == "automacao", r
    run.reabrir()
    x = run(f"/api/automacoes/{aid}")[1]
    assert x["entao"][0]["params"]["titulo"] == "Conta: {remetente}"
    assert x["quando"]["palavras"] == ["boleto", "fatura", "conta"]


def test_automacao_editada_que_vira_arvore_propria(run):
    aid = run("/api/automacoes", _auto())[1]["id"]
    g = run(f"/api/automacoes/{aid}/arvore")[1]["graph"]
    # um "não" ligado a outra ação: uma automação não representa isso -> vira árvore própria
    filtro = next(n for n in g["nodes"] if n["kind"] == "if")
    g["nodes"].append({"id": "outro", "kind": "action", "props": {"action": "log", "message": "{title}"},
                       "ports": ["then"], "list_ports": [], "x": 0, "y": 0})
    g["edges"].append({"from": filtro["id"], "to": "outro", "label": "false", "key": False})
    c, r = run(f"/api/automacoes/{aid}/arvore", {"graph": g, "nome": "boletos avançado"})
    assert c == 200 and r["modo"] == "arvore", r
    run.reabrir()
    assert run(f"/api/automacoes/{aid}")[1]["ativa"] is False
    names = [x["name"] for x in run("/api/trees/graph")[1]["graphs"]]
    assert "boletos avançado" in names


def test_arvore_editar_renomear_e_acoes_novas(run):
    tg = run.app.service("treegraph")
    tree = {"name": "minha regra", "start": "q", "nodes": {
        "q": {"if": {"contains_any": ["urgente"], "categoria_in": ["trabalho"]}, "branches": {"true": "a"}},
        "a": {"action": "criar_card", "titulo": "Urgente: {title}", "campos": ["prazo"]}}}
    g = tg.tree_to_graph(tree)
    c, r = run("/api/trees/graph", {"graph": g, "name": "minha regra"})
    assert c == 200, r                                   # ações das automações agora são válidas no editor
    g["nodes"][1]["props"]["titulo"] = "Muito urgente: {title}"
    c, r = run("/api/trees/graph", {"graph": g, "name": "regra renomeada", "original": "minha regra"})
    assert c == 200, r
    run.reabrir()
    gs = run("/api/trees/graph")[1]["graphs"]
    assert [x["name"] for x in gs] == ["regra renomeada"]   # renomear substitui, não duplica
    a = next(n for n in gs[0]["nodes"] if n["id"] == "a")
    assert a["props"]["titulo"] == "Muito urgente: {title}"
    # erro de validação vem em linguagem simples, apontando o passo
    g2 = dict(gs[0], name="ruim")
    g2["nodes"] = [dict(n, props=dict(n["props"], action="etiquetar", etiqueta="")) if n["id"] == "a" else n
                   for n in g2["nodes"]]
    c, r = run("/api/trees/graph", {"graph": g2, "name": "ruim"})
    assert c == 400 and any(e["node"] == "a" and "etiqueta" in e["message"] for e in r["errors"])


def test_api_local_desligar_e_religar_persiste(run):
    c, r = run("/api/local_api", {"modelo": "flash", "ativa": False, "porta": 8761})
    assert c == 200 and r["ativa"] is False, r
    run.reabrir()
    x = next(a for a in run("/api/local_api")[1]["apis"] if a["modelo"] == "flash")
    assert x["ativa"] is False and x["porta"] == 8761
    run("/api/local_api", {"modelo": "flash", "ativa": True})
    run.reabrir()
    x = next(a for a in run("/api/local_api")[1]["apis"] if a["modelo"] == "flash")
    assert x["ativa"] is True                            # antes a lista "desligadas" antiga ficava no config.yaml


def test_leveza_iniciar_com_windows_persiste(run):
    c, r = run("/api/app/iniciar_com_windows", {"ativo": False})
    assert c == 200, r
    run.reabrir()
    assert run("/api/app/recursos")[1]["config"]["iniciar_com_windows"] is False


def test_voz_preferencias_persistem(run):
    c, r = run("/api/voice/prefs", {"voz": "Microsoft Daniel", "velocidade": 1.3})
    if c == 503:
        pytest.skip("voz indisponível neste ambiente")
    assert c == 200, r
    run.reabrir()
    st = run("/api/voice/status")
    if st[0] == 503:
        pytest.skip("voz indisponível neste ambiente")
    assert st[1]["prefs"] == {"voz": "Microsoft Daniel", "velocidade": 1.3}
    assert run("/api/voice/prefs", {"velocidade": 9})[0] == 400
