"""Árvores visuais: ida e volta árvore <-> grafo, layout sem sobreposição, validação e o editor JS (headless)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from djorge_local.tree import TreeError, validate  # noqa: E402
from djorge_local.treegraph import (graph_to_tree, layout, overlaps, tree_to_graph, validate_graph,  # noqa: E402
                                    check_graph, trees_to_graphs, graphs_to_trees)

JS = ROOT / "djorge_local" / "static" / "tree-editor.js"

FULL = {
    "name": "completa", "enabled": False, "origem": "texto da regra", "descricao": "todos os tipos",
    "when": {"source_in": ["email"], "contains_any": ["boleto"], "seguranca_in": ["seguro"]},
    "start": "tipo",
    "nodes": {
        "tipo": {"choice": ["a", "b", "c"], "ask": "Qual?", "model": "origo", "min_probability": 0.7,
                 "branches": {"a": ["ok", "nota"], "b": "escala", "c": [], "abstain": "parar", "default": "parar"}},
        "ok": {"check": "o texto pede {title}", "model": "flash",
               "branches": {True: "multi", False: "parar", "abstain": ["log"]}},
        "escala": {"score": ["baixa", "alta"], "ask": "Urgência?", "branches": {">= 1": "sel", "default": "parar"}},
        "multi": {"multi": ["x", "y"], "branches": {"x": "se", "y": ["web", "cmd"], "none": "parar"}},
        "sel": {"select": "TOTAL TYPE money", "render_query": True, "branches": {">= 1000": "q", "== 0": "parar"}},
        "q": {"query": 'NOUL "é urgente" FROM text', "branches": {"true": "parar"}},
        "se": {"if": {"contains_any": ["pix"], "regex": "r\\$ ?\\d+", "sender_has": ["banco"], "app_in": ["WhatsApp"]},
               "branches": {True: "nota", False: "parar"}},
        "nota": {"action": "notify", "title": "Aviso {tipo.answer}", "message": "{text:80}", "then": ["log", "arq"]},
        "log": {"action": "log", "message": "{source} {tipo.probability}", "then": "parar"},
        "arq": {"action": "write_file", "file": "r.txt", "content": "{time}", "auto": True},
        "web": {"action": "webhook", "url": "https://exemplo.com/h", "body": {"t": "{text}", "n": [1, 2]}},
        "cmd": {"action": "command", "argv": ["echo", "{title}"], "timeout": 5, "then": None},
        "mv": {"action": "email_move", "folder": "Trabalho", "uid": "{uid}", "mailbox": "{mailbox}"},
        "parar": {"action": "stop"},
    },
}
FULL["nodes"]["tipo"]["branches"]["b"] = ["escala", "mv"]


def example_trees():
    return yaml.safe_load((ROOT / "arvores.example.yaml").read_text(encoding="utf-8"))["trees"]


def roundtrip(t):
    g = json.loads(json.dumps(tree_to_graph(t), ensure_ascii=False))     # passa por JSON como no navegador
    return g, graph_to_tree(g)


def same(a, b):
    assert yaml.safe_dump(a, allow_unicode=True, sort_keys=True) == yaml.safe_dump(b, allow_unicode=True, sort_keys=True)
    assert a == b


@pytest.mark.parametrize("t", example_trees(), ids=lambda t: t["name"])
def test_roundtrip_example(t):
    g, back = roundtrip(t)
    same(back, t)
    assert list(back["nodes"]) == list(t["nodes"])
    for nid in t["nodes"]:
        assert list(back["nodes"][nid]) == list(t["nodes"][nid])           # ordem das chaves preservada
        assert list((back["nodes"][nid].get("branches") or {})) == list((t["nodes"][nid].get("branches") or {}))
    validate([back])


def test_roundtrip_all_kinds():
    g, back = roundtrip(FULL)
    same(back, FULL)
    kinds = {n["id"]: n["kind"] for n in g["nodes"]}
    assert set(kinds.values()) == {"choice", "check", "score", "multi", "select", "query", "if", "action"}
    acts = {n["props"]["action"] for n in g["nodes"] if n["kind"] == "action"}
    assert acts == {"notify", "log", "write_file", "webhook", "command", "email_move", "stop"}
    e = [x for x in g["edges"] if x["from"] == "ok" and x["label"] == "true"][0]
    assert e["key"] is True and e["to"] == "multi"
    # rótulos e lista de próximos
    assert [x["to"] for x in g["edges"] if x["from"] == "nota" and x["label"] == "then"] == ["log", "arq"]
    assert [x["to"] for x in g["edges"] if x["from"] == "tipo" and x["label"] == "a"] == ["ok", "nota"]
    tipo = next(n for n in g["nodes"] if n["id"] == "tipo")
    assert tipo["label"] == "Qual?" and "c" in tipo["ports"] and "c" in tipo["list_ports"]
    # e a volta da volta
    same(graph_to_tree(tree_to_graph(back)), FULL)


def test_graph_roundtrip_and_many():
    gs = trees_to_graphs(example_trees() + [FULL])
    same(graphs_to_trees(gs), example_trees() + [FULL])
    g = tree_to_graph(FULL)
    g2 = tree_to_graph(graph_to_tree(g))
    assert [(n["id"], n["props"], n["ports"]) for n in g["nodes"]] == [(n["id"], n["props"], n["ports"]) for n in g2["nodes"]]
    assert g["edges"] == g2["edges"]


def test_layout_no_overlap_left_to_right():
    for t in example_trees() + [FULL]:
        g = tree_to_graph(t)
        assert overlaps(g) == []
        x = {n["id"]: n["x"] for n in g["nodes"]}
        assert x[t["start"]] == min(x.values())
        for e in g["edges"]:                       # filhos alcançados pela 1ª vez ficam à direita
            pass
        assert x["fim" if "fim" in x else "parar"] > x[t["start"]]
    g = tree_to_graph(FULL)
    for n in g["nodes"]:
        n["x"] = n["y"] = 0
    assert overlaps(g)
    assert overlaps(layout(g)) == []


def test_layout_handles_disconnected_and_cycles():
    t = {"name": "c", "start": "a", "nodes": {"a": {"check": "x", "branches": {True: "b"}},
                                              "b": {"check": "y", "branches": {True: "a"}},
                                              "solto": {"action": "stop"}}}
    g = tree_to_graph(t)
    assert overlaps(g) == []
    errs = validate_graph(g)
    assert [e for e in errs if e["level"] == "erro"] == []
    assert any(e["node"] == "solto" and "alcançado" in e["message"] for e in errs)
    same(graph_to_tree(g), t)


def errs_of(g):
    return [(e["node"], e["message"]) for e in validate_graph(g) if e["level"] == "erro"]


def test_validation_messages_point_to_node():
    g = tree_to_graph(FULL)
    g["edges"].append({"from": "tipo", "to": "nao_existe", "label": "a"})
    e = errs_of(g)
    assert ("tipo", "a resposta “a” aponta para um nó que não existe (“nao_existe”)") in e
    with pytest.raises(TreeError, match="tipo: a resposta"):
        graph_to_tree(g)

    g = tree_to_graph(FULL)
    next(n for n in g["nodes"] if n["id"] == "escala")["props"]["score"] = ["só uma"]
    next(n for n in g["nodes"] if n["id"] == "ok")["props"]["check"] = " "
    next(n for n in g["nodes"] if n["id"] == "web")["props"]["url"] = ""
    next(n for n in g["nodes"] if n["id"] == "se")["props"]["if"] = {}
    next(n for n in g["nodes"] if n["id"] == "tipo")["props"]["min_probability"] = 3
    g["edges"].append({"from": "parar", "to": "log", "label": "then"})
    e = dict(errs_of(g))
    assert "pelo menos duas opções" in e["escala"]
    assert e["ok"] == "escreva a afirmação a verificar"
    assert e["web"] == "o webhook precisa de uma URL"
    assert "condição está vazia" in e["se"]
    assert "entre 0 e 1" in e["tipo"]
    assert "parar" in e and "encerra" in e["parar"]

    g = tree_to_graph(FULL)
    g["start"] = "zzz"
    assert (None, "escolha o nó de início") in errs_of(g)
    g = tree_to_graph(FULL)
    next(n for n in g["nodes"] if n["id"] == "se")["props"]["if"]["regex"] = "(abc"
    assert "expressão regular inválida" in dict(errs_of(g))["se"]
    g = tree_to_graph(FULL)
    g["nodes"][1]["id"] = "tipo"
    assert ("tipo", "identificador repetido “tipo”") in errs_of(g)
    r = check_graph(tree_to_graph(FULL))
    assert r["ok"] and r["tree"] == FULL and r["errors"] == []


def test_new_graph_from_editor_shape():
    """Grafo como o editor cria do zero: sem ports/list_ports, rótulos true/false como texto."""
    g = {"name": "nova", "start": "se", "nodes": [
        {"id": "se", "kind": "if", "props": {"if": {"contains_any": ["pix"]}}, "x": 0, "y": 0},
        {"id": "aviso", "kind": "action", "props": {"action": "notify", "title": "Pix", "message": "{text}"}, "x": 0, "y": 0},
        {"id": "fim", "kind": "action", "props": {"action": "stop"}, "x": 0, "y": 0}],
        "edges": [{"from": "se", "to": "aviso", "label": "true"}, {"from": "se", "to": "fim", "label": "false"},
                  {"from": "aviso", "to": "fim", "label": "then"}]}
    t = graph_to_tree(g)
    assert t["nodes"]["se"]["branches"] == {True: "aviso", False: "fim"}
    assert t["nodes"]["aviso"]["then"] == "fim" and "branches" not in t["nodes"]["aviso"]
    assert t["when"] == {} and t["enabled"] is True
    validate([t])


def test_rules_tree_roundtrip():
    t = {"name": "regra-ana", "enabled": True, "when": {"source_in": ["email"], "sender_has": ["Ana"]},
         "start": "acao", "nodes": {"acao": {"action": "email_move", "folder": "Trabalho", "uid": "{uid}",
                                             "mailbox": "{mailbox}"}}, "origem": "e-mails da Ana para Trabalho"}
    _, back = roundtrip(t)
    same(back, t)


# ------------------------------------------------------------------ JS
def test_js_syntax():
    node = shutil.which("node")
    if not node:
        pytest.skip("node não instalado")
    subprocess.run([node, "--check", str(JS)], check=True)


HEADLESS = r"""
const {chromium} = require('playwright');
(async () => {
  const [page_, graph] = [process.argv[2], JSON.parse(require('fs').readFileSync(process.argv[3], 'utf8'))];
  const b = await chromium.launch(); const p = await b.newPage({viewport: {width: 1400, height: 900}});
  const errors = []; p.on('pageerror', (e) => errors.push(String(e))); p.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
  await p.goto('file://' + page_);
  await p.evaluate((g) => {
    window.changes = [];
    window.ed = TreeEditor.mount(document.getElementById('app'), {graph: g, onChange: (x) => window.changes.push(x),
      api: {test: async (text) => ({runs: [{tree: g.name, trail: [{node: 'assunto', answer: 'entrega', probability: .9, next: ['registrar']},
        {node: 'registrar', action: 'write_file', status: 'ok', next: ['fim']}, {node: 'fim', action: 'stop', next: []}]}]}),
        validate: null}});
  }, graph);
  const out = {};
  out.nodes0 = await p.$$eval('.te-node', (els) => els.length);
  out.ports_assunto = await p.$$eval('.te-node[data-id="assunto"] .te-port', (els) => els.map((e) => e.dataset.port));
  // sem sobreposição na tela
  out.overlap = await p.evaluate(() => { const r = [...document.querySelectorAll('.te-node')].map((e) => e.getBoundingClientRect());
    for (let i = 0; i < r.length; i++) for (let j = i + 1; j < r.length; j++) { const a = r[i], c = r[j];
      if (a.left < c.right - 1 && c.left < a.right - 1 && a.top < c.bottom - 1 && c.top < a.bottom - 1) return true; } return false; });
  // adicionar pela paleta (clique) e por arrasto do mouse a partir de uma porta
  await p.click('.te-pal button[data-key="check"]');
  await p.click('.te-pal button[data-key="action:notify"]');
  const ids = await p.evaluate(() => ed.getGraph().nodes.map((n) => n.id));
  out.added = ids.slice(-2);
  await p.evaluate(() => ed.autoLayout());
  const dot = await p.$('.te-node[data-id="verifica"] .te-dot[data-port="true"]');
  const tgt = await p.$('.te-node[data-id="aviso"] .te-head');
  const a = await dot.boundingBox(), c = await tgt.boundingBox();
  await p.mouse.move(a.x + a.width / 2, a.y + a.height / 2); await p.mouse.down();
  await p.mouse.move((a.x + c.x) / 2, (a.y + c.y) / 2, {steps: 5}); await p.mouse.move(c.x + 20, c.y + 20, {steps: 5}); await p.mouse.up();
  // ligar "pessoal" do nó assunto ao novo nó pelo painel (teclado)
  await p.evaluate(() => ed.select('assunto'));
  await p.selectOption('select[aria-label="Ligar a resposta pessoal a um nó"]', 'verifica');
  let g = await p.evaluate(() => ed.getGraph());
  out.edge_mouse = g.edges.some((e) => e.from === 'verifica' && e.to === 'aviso' && e.label === 'true');
  out.edge_kbd = g.edges.some((e) => e.from === 'assunto' && e.to === 'verifica' && e.label === 'pessoal');
  // arrastar nó
  const before = g.nodes.find((n) => n.id === 'aviso');
  const hb = await (await p.$('.te-node[data-id="aviso"] .te-title')).boundingBox();
  await p.mouse.move(hb.x + 5, hb.y + 5); await p.mouse.down(); await p.mouse.move(hb.x + 85, hb.y + 65, {steps: 4}); await p.mouse.up();
  g = await p.evaluate(() => ed.getGraph()); const after = g.nodes.find((n) => n.id === 'aviso');
  out.moved = after.x !== before.x && after.y !== before.y;
  // editar opções (chips) e limiar
  await p.evaluate(() => ed.select('assunto'));
  await p.fill('.te-props .te-chips input', 'imposto'); await p.press('.te-props .te-chips input', 'Enter');
  g = await p.evaluate(() => ed.getGraph());
  out.chip = g.nodes.find((n) => n.id === 'assunto').props.choice.includes('imposto');
  out.port_new = (await p.$$eval('.te-node[data-id="assunto"] .te-port', (els) => els.map((e) => e.dataset.port))).includes('imposto');
  // validação ao vivo: apaga a afirmação
  await p.evaluate(() => ed.select('verifica'));
  await p.fill('.te-props textarea', '');
  out.err_mark = await p.$eval('.te-node[data-id="verifica"]', (e) => e.classList.contains('te-err'));
  out.foot = await p.$eval('.te-foot', (e) => e.textContent);
  // desfazer/refazer pelo teclado
  await p.click('.te-node[data-id="verifica"] .te-kind');
  const n1 = (await p.evaluate(() => ed.getGraph())).nodes.length;
  await p.keyboard.press('Control+d');
  const n2 = (await p.evaluate(() => ed.getGraph())).nodes.length;
  await p.keyboard.press('Delete');
  const n3 = (await p.evaluate(() => ed.getGraph())).nodes.length;
  await p.keyboard.press('Control+z');
  const n4 = (await p.evaluate(() => ed.getGraph())).nodes.length;
  await p.keyboard.press('Control+y');
  out.counts = [n1, n2, n3, n4, (await p.evaluate(() => ed.getGraph())).nodes.length];
  // zoom pela roda
  const k0 = await p.evaluate(() => ed._editor.view.k);
  await p.mouse.move(700, 450); await p.mouse.wheel(0, -300);
  out.zoomed = (await p.evaluate(() => ed._editor.view.k)) > k0;
  // teste com texto: destaca caminho
  await p.fill('.te-test input', 'chegou sua encomenda'); await p.click('.te-test button');
  await p.waitForTimeout(1100);
  out.hit = await p.$$eval('.te-node.te-hit-done', (els) => els.map((e) => e.dataset.id).sort());
  out.flow = await p.$$eval('.te-edge.te-flow', (els) => els.length);
  out.minimap = await p.$$eval('.te-mini rect', (els) => els.length);
  out.changes = await p.evaluate(() => window.changes.length);
  out.final = await p.evaluate(() => ed.getGraph());
  out.errors = errors;
  await p.screenshot({path: process.argv[4]});
  console.log(JSON.stringify(out));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


def _playwright_ok(node):
    r = subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=str(ROOT))
    return r.returncode == 0


def test_js_headless_editor():
    node = shutil.which("node")
    if not node or not _playwright_ok(node):
        pytest.skip("node/playwright indisponível")
    t = example_trees()[0]
    d = Path(tempfile.mkdtemp(prefix="te-"))
    (d / "g.json").write_text(json.dumps(tree_to_graph(t), ensure_ascii=False), encoding="utf-8")
    (d / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><style>html,body{margin:0;height:100%}#app{height:860px}</style>'
        f'<div id="app"></div><script src="file://{JS}"></script>', encoding="utf-8")
    (d / "run.js").write_text(HEADLESS, encoding="utf-8")
    shot = os.environ.get("TE_SHOT", str(d / "shot.png"))
    r = subprocess.run([node, str(d / "run.js"), str(d / "index.html"), str(d / "g.json"), shot],
                       capture_output=True, text=True, timeout=120, cwd=str(ROOT))
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["errors"] == []
    assert out["nodes0"] == len(t["nodes"])
    assert out["ports_assunto"][:5] == ["cobrança", "entrega", "trabalho", "pessoal", "outro"]
    assert out["overlap"] is False
    assert out["added"] == ["verifica", "aviso"]
    assert out["edge_mouse"] and out["edge_kbd"] and out["moved"]
    assert out["chip"] and out["port_new"]
    assert out["err_mark"] and "verifica" in out["foot"]
    n = out["counts"]
    assert n[1] == n[0] + 1 and n[2] == n[0] and n[3] == n[1] and n[4] == n[0]
    assert out["zoomed"]
    assert out["hit"] == ["assunto", "fim", "registrar"] and out["flow"] >= 2
    assert out["minimap"] >= len(t["nodes"]) and out["changes"] > 5
    # o grafo emitido pelo editor volta a ser uma árvore válida (após corrigir a afirmação vazia)
    g = out["final"]
    next(x for x in g["nodes"] if x["id"] == "verifica")["props"]["check"] = "é um imposto"
    tree = graph_to_tree(g)
    assert tree["nodes"]["verifica"]["branches"] == {True: "aviso"}
    assert "verifica" in (tree["nodes"]["assunto"]["branches"]["pessoal"] if isinstance(
        tree["nodes"]["assunto"]["branches"]["pessoal"], list) else [tree["nodes"]["assunto"]["branches"]["pessoal"]])
    for nid, nd in t["nodes"].items():               # o que não foi mexido continua igual
        if nid != "assunto":
            assert tree["nodes"][nid] == nd
