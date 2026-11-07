"""Árvores de decisão <-> grafos para o editor visual (static/tree-editor.js).

Grafo de uma árvore:
  {"name", "enabled", "when", "start", "extra": {outras chaves da árvore},
   "nodes": [{"id", "kind", "label", "props", "ports", "list_ports", "x", "y"}],
   "edges": [{"from", "to", "label", "key"?}]}

- kind: choice | check | score | multi | select | query | if | action (o tipo da ação fica em props.action).
- props: todas as chaves do nó exceto branches/then (opções, ask, model, min_probability, placeholders...).
- ports: as respostas do nó na ordem original (inclui ramos vazios); "then" para ações.
- list_ports: portas cujo valor era uma lista (then/next em lista), para a volta ser idêntica.
- edges: uma por destino; várias com o mesmo label = lista de próximos, na ordem.
  "key" guarda a chave original quando ela não era texto (ex.: true/false do YAML).

tree_to_graph(graph_to_tree(g)) e graph_to_tree(tree_to_graph(t)) não perdem nada.
"""
from __future__ import annotations

import copy
from collections import deque
from typing import Any, Dict, List

from .tree import COND_KEYS, NOVAS_ACOES, TRATAMENTOS, TreeError, regex_segura, validate

KINDS = ("choice", "check", "score", "multi", "select", "query", "if", "action")
ACTIONS = ("notify", "log", "write_file", "webhook", "command", "stop", "email_move", "tratar") + tuple(NOVAS_ACOES)
KIND_NAMES = {"choice": "Pergunta de escolha", "check": "Verificação sim/não", "score": "Escala",
              "multi": "Múltipla escolha", "select": "Seleção/quantidade", "query": "Consulta avançada",
              "if": "Condição (se)", "action": "Ação"}
ACTION_NAMES = {"notify": "Notificar", "log": "Registrar no histórico", "write_file": "Salvar em arquivo",
                "webhook": "Webhook", "command": "Comando", "stop": "Parar", "email_move": "Mover e-mail",
                "criar_card": "Criar card", "tarefa": "Criar tarefa", "alerta": "Criar alerta", "notificar": "Avisar",
                "etiquetar": "Etiquetar", "arquivar": "Arquivar", "abrir_app": "Abrir app",
                "responder_sugestao": "Sugerir resposta", "destacar": "Destacar", "lembrete": "Criar lembrete",
                "nota": "Criar nota", "atualizar_card": "Atualizar card", "mover_card": "Mover card",
                "concluir_card": "Concluir card", "prioridade": "Mudar prioridade", "abrir_site": "Abrir site",
                "ignorar": "Ignorar", "encadear": "Rodar outra automação", "adicionar_lista": "Adicionar à lista",
                "registrar": "Registrar no histórico", "tratar": "Tratar"}
TRAT_NAMES = {"extrair": "Extrair campos", "classificar": "Classificar", "resumir": "Resumir (frase central)",
              "formatar": "Formatar", "agrupar": "Agrupar e resumir"}
TREE_KEYS = ("name", "enabled", "when", "start", "nodes")

NODE_W, NODE_H, PORT_H, GAP_X, GAP_Y = 240, 64, 26, 90, 40


def node_kind(n: dict) -> str:
    for k in KINDS:
        if k in n:
            return k
    raise TreeError("nó sem tipo (use choice, check, score, multi, select, query, if ou action)")


def _label(nid: str, kind: str, n: dict) -> str:
    if kind == "action" and n.get("action") == "tratar":
        base = TRAT_NAMES.get(n.get("tipo"), "Tratar")
        if n.get("tipo") == "classificar" and n.get("opcoes"):
            return f"{base}: {' / '.join(map(str, n['opcoes']))}"
        if n.get("tipo") == "extrair" and n.get("campos"):
            return f"{base}: {', '.join(map(str, n['campos']))}"
        if n.get("tipo") == "agrupar":
            return f"{base} a cada {n.get('minutos', 30)} min"
        return base
    if kind == "if" and isinstance(n.get("if"), dict) and n["if"].get("count_min"):
        c = n["if"]["count_min"]
        return f"Mais de {c.get('n')} em {c.get('minutos')} min"
    if kind == "action":
        for v in (n.get("title"), n.get("titulo"), n.get("etiqueta"), n.get("file"), n.get("url"), n.get("folder"), n.get("message"),
                  " ".join(map(str, n.get("argv") or []))):
            if isinstance(v, str) and v.strip():
                return v
        return ACTION_NAMES.get(n.get("action"), str(n.get("action")))
    if n.get("ask"):
        return str(n["ask"])
    if kind in ("check", "select", "query") and isinstance(n.get(kind), str) and n[kind].strip():
        return n[kind]
    return KIND_NAMES.get(kind, kind)


def default_ports(node: dict) -> List[str]:
    """As mesmas portas que o editor mostra (tree-editor.js defaultPorts)."""
    k, p = node.get("kind"), node.get("props") or {}
    if k in ("choice", "score"):
        return [str(x) for x in p.get(k) or []] + ["abstain", "default"]
    if k == "multi":
        return [str(x) for x in p.get(k) or []] + ["none", "abstain"]
    return {"check": ["true", "false", "abstain"], "if": ["true", "false"], "select": ["default", "abstain"],
            "query": ["default", "abstain"]}.get(k, [] if p.get("action") == "stop" else ["then"])


def display_ports(node: dict, edges: List[dict] = ()) -> List[str]:
    out: List[str] = []
    for x in list(node.get("ports") or []) + default_ports(node) + [e.get("label") for e in edges
                                                                     if e.get("from") == node.get("id")]:
        if x is not None and str(x) not in out:
            out.append(str(x))
    if node.get("kind") == "action" and (node.get("props") or {}).get("action") == "stop":
        out = [x for x in out if any(e.get("from") == node.get("id") and e.get("label") == x for e in edges)]
    tail = ["none", "abstain", "default"]
    return [x for x in out if x not in tail] + [x for x in tail if x in out]


def node_size(node: dict, edges: List[dict] = ()) -> tuple:
    return NODE_W, NODE_H + PORT_H * max(1, len(display_ports(node, edges)))


# ------------------------------------------------------------------ ida
def tree_to_graph(tree: dict) -> dict:
    nodes, edges = [], []
    for nid, n in (tree.get("nodes") or {}).items():
        kind = node_kind(n)
        props = {k: copy.deepcopy(v) for k, v in n.items() if k not in ("branches", "then")}
        ports, list_ports = [], []
        conns = list((n.get("branches") or {}).items())
        if "then" in n:
            conns.append(("then", n["then"]))
        has_branches = "branches" in n
        for key, val in conns:
            label = _key_str(key)
            ports.append(label)
            if isinstance(val, list):
                list_ports.append(label)
            for to in (val if isinstance(val, list) else ([] if val is None else [val])):
                e = {"from": nid, "to": to, "label": label}
                if not isinstance(key, str):
                    e["key"] = key
                edges.append(e)
            if val is None:
                props.setdefault("_null_ports", []).append(label)
        node = {"id": nid, "kind": kind, "label": _label(nid, kind, n), "props": props, "ports": ports,
                "list_ports": list_ports, "x": 0, "y": 0}
        if has_branches and not n.get("branches"):
            node["empty_branches"] = True
        nodes.append(node)
    g = {"name": tree.get("name"), "enabled": tree.get("enabled", True), "when": copy.deepcopy(tree.get("when") or {}),
         "start": tree.get("start"), "extra": {k: copy.deepcopy(v) for k, v in tree.items() if k not in TREE_KEYS},
         "nodes": nodes, "edges": edges}
    g["_has"] = [k for k in ("enabled", "when") if k in tree]
    return layout(g)


def _key_str(k: Any) -> str:
    if isinstance(k, bool):
        return "true" if k else "false"
    return str(k)


# ------------------------------------------------------------------ volta
def graph_to_tree(graph: dict, check: bool = True) -> dict:
    ids = [n["id"] for n in graph.get("nodes") or []]
    nodes: Dict[str, dict] = {}
    for gn in graph.get("nodes") or []:
        props = copy.deepcopy(gn.get("props") or {})
        nulls = props.pop("_null_ports", [])
        kind = gn.get("kind")
        if kind == "action":
            props.setdefault("action", "stop")
        elif kind in KINDS and kind not in props:
            props[kind] = [] if kind in ("choice", "score", "multi") else ({} if kind == "if" else "")
        outs = [e for e in graph.get("edges") or [] if e.get("from") == gn["id"]]
        ports = list(gn.get("ports") or [])
        for e in outs:                                   # portas criadas só por arestas
            if e.get("label") not in ports:
                ports.append(e.get("label"))
        lp = set(gn.get("list_ports") or [])
        branches = {}
        for p in ports:
            targets = [e["to"] for e in outs if e.get("label") == p]
            key = next((e["key"] for e in outs if e.get("label") == p and "key" in e), None)
            if key is None:
                key = (p == "true") if (p in ("true", "false") and kind in ("check", "if")) else p
            if p in nulls and not targets:
                val = None
            elif p in lp or len(targets) > 1:
                val = targets
            elif targets:
                val = targets[0]
            else:
                continue                                  # porta sem ligação: some
            if p == "then":
                props["then"] = val
            else:
                branches[key] = val
        if branches or gn.get("empty_branches"):
            props["branches"] = branches
        # ordem estável: chaves originais, branches, then
        then = props.pop("then", None) if "then" in props else _MISSING
        if "branches" in props:
            props["branches"] = props.pop("branches")
        if then is not _MISSING:
            props["then"] = then
        nodes[gn["id"]] = props
    tree: Dict[str, Any] = {"name": graph.get("name")}
    has = graph.get("_has", ["enabled", "when"])
    if "enabled" in has or graph.get("enabled") is False:
        tree["enabled"] = graph.get("enabled", True)
    if "when" in has or graph.get("when"):
        tree["when"] = copy.deepcopy(graph.get("when") or {})
    tree["start"] = graph.get("start") or (ids[0] if ids else None)
    tree["nodes"] = nodes
    tree.update(copy.deepcopy(graph.get("extra") or {}))
    if check:
        errs = [e for e in validate_graph(graph, tree) if e["level"] == "erro"]
        if errs:
            raise TreeError("; ".join(f"{e['node'] or 'árvore'}: {e['message']}" for e in errs))
    return tree


_MISSING = object()


def trees_to_graphs(trees: List[dict]) -> List[dict]:
    return [tree_to_graph(t) for t in trees]


def graphs_to_trees(graphs: List[dict]) -> List[dict]:
    trees = [graph_to_tree(g) for g in graphs]
    validate(trees)
    return trees


# ------------------------------------------------------------------ layout
def layout(graph: dict) -> dict:
    """Camadas da esquerda para a direita (profundidade a partir do início); sem sobreposição."""
    nodes = graph.get("nodes") or []
    by = {n["id"]: n for n in nodes}
    out: Dict[str, List[str]] = {n["id"]: [] for n in nodes}
    for e in graph.get("edges") or []:
        if e.get("from") in out and e.get("to") in by and e["to"] not in out[e["from"]]:
            out[e["from"]].append(e["to"])
    depth: Dict[str, int] = {}
    roots = [graph.get("start")] if graph.get("start") in by else []
    roots += [n["id"] for n in nodes if n["id"] not in roots]
    for r in roots:
        if r in depth:
            continue
        base = (max(depth.values()) + 1) if depth else 0
        depth[r] = base
        q = deque([r])
        while q:                                         # BFS: cada nó na menor camada em que aparece
            cur = q.popleft()
            for nx in out[cur]:
                if nx not in depth:
                    depth[nx] = depth[cur] + 1
                    q.append(nx)
    cols: Dict[int, List[str]] = {}
    for nid in [n["id"] for n in nodes]:
        cols.setdefault(depth[nid], []).append(nid)
    # ordena cada coluna pela posição média dos pais (menos cruzamentos)
    order: Dict[str, float] = {}
    for d in sorted(cols):
        if d:
            def key(nid):
                ps = [order[p] for p, cs in out.items() if nid in cs and p in order]
                return sum(ps) / len(ps) if ps else 1e9
            cols[d].sort(key=key)
        y = 0.0
        for i, nid in enumerate(cols[d]):
            order[nid] = i
            n = by[nid]
            w, h = node_size(n, graph.get("edges") or [])
            n["x"], n["y"] = d * (NODE_W + GAP_X), y
            y += h + GAP_Y
    # centraliza colunas verticalmente
    E = graph.get("edges") or []
    hs = {d: sum(node_size(by[i], E)[1] + GAP_Y for i in c) - GAP_Y for d, c in cols.items()}
    top = max(hs.values()) if hs else 0
    for d, c in cols.items():
        off = (top - hs[d]) / 2
        for nid in c:
            by[nid]["y"] = round(by[nid]["y"] + off)
    return graph


def overlaps(graph: dict) -> List[tuple]:
    ns, E = graph.get("nodes") or [], graph.get("edges") or []
    bad = []
    for i, a in enumerate(ns):
        aw, ah = node_size(a, E)
        for b in ns[i + 1:]:
            bw, bh = node_size(b, E)
            if a["x"] < b["x"] + bw and b["x"] < a["x"] + aw and a["y"] < b["y"] + bh and b["y"] < a["y"] + ah:
                bad.append((a["id"], b["id"]))
    return bad


# ------------------------------------------------------------------ validação
def validate_graph(graph: dict, tree: dict = None) -> List[dict]:
    """Erros em português, cada um apontando o nó: [{"node": id|None, "message": str}]."""
    errs: List[dict] = []
    add = lambda nid, msg, level="erro": errs.append({"node": nid, "message": msg, "level": level})
    if not str(graph.get("name") or "").strip():
        add(None, "a árvore precisa de um nome")
    ids = [n.get("id") for n in graph.get("nodes") or []]
    if not ids:
        add(None, "a árvore não tem nenhum nó")
    seen = set()
    for n in graph.get("nodes") or []:
        nid, kind, p = n.get("id"), n.get("kind"), n.get("props") or {}
        if not nid or not str(nid).strip():
            add(None, "há um nó sem identificador"); continue
        if nid in seen:
            add(nid, f"identificador repetido “{nid}”")
        seen.add(nid)
        if kind not in KINDS:
            add(nid, f"tipo de nó desconhecido “{kind}”"); continue
        if kind in ("choice", "score", "multi"):
            opts = p.get(kind)
            if not isinstance(opts, list) or len([o for o in opts if str(o).strip()]) < 2:
                add(nid, f"{KIND_NAMES[kind].lower()} precisa de pelo menos duas opções")
        elif kind in ("check", "select", "query"):
            if not str(p.get(kind) or "").strip():
                add(nid, {"check": "escreva a afirmação a verificar", "select": "escreva o seletor (ex.: TOTAL TYPE money)",
                          "query": "escreva a consulta"}[kind])
        elif kind == "if":
            c = p.get("if")
            if not isinstance(c, dict) or not any(c.get(k) not in (None, "", [], {}, False) for k in COND_KEYS):
                add(nid, "a condição está vazia: diga o que procurar no texto, remetente, app ou origem")
            elif c.get("regex"):
                try:
                    regex_segura(c["regex"])
                except TreeError as e:
                    m = str(e)
                    add(nid, ("expressão regular inválida: " + m.split(": ", 1)[1]) if m.startswith("regex inválida")
                        else m)
        elif kind == "action":
            a = p.get("action")
            if a not in ACTIONS:
                add(nid, f"ação desconhecida “{a}”")
            elif a == "webhook" and not str(p.get("url") or "").strip():
                add(nid, "o webhook precisa de uma URL")
            elif a == "command" and not p.get("argv"):
                add(nid, "o comando precisa do programa e dos argumentos")
            elif a == "write_file" and not str(p.get("file") or "").strip():
                add(nid, "diga em qual arquivo salvar")
            elif a == "email_move" and not str(p.get("folder") or "").strip():
                add(nid, "diga para qual pasta mover o e-mail")
            elif a == "etiquetar" and not str(p.get("etiqueta") or "").strip():
                add(nid, "diga qual etiqueta pôr")
            elif a == "tratar" and p.get("tipo") not in TRATAMENTOS:
                add(nid, "escolha o tratamento: extrair, classificar, resumir, formatar ou agrupar")
            elif a == "tratar" and p.get("tipo") == "classificar" and len([o for o in p.get("opcoes") or []
                                                                         if str(o).strip()]) < 2:
                add(nid, "classificar precisa de pelo menos duas opções")
            elif a == "abrir_site" and not str(p.get("url") or "").startswith(("https://", "http://")):
                add(nid, "diga o endereço do site (https://…)")
            elif a == "encadear" and not str(p.get("automacao") or "").strip():
                add(nid, "diga qual automação rodar")
            elif a == "adicionar_lista" and not str(p.get("lista") or "").strip():
                add(nid, "diga em qual lista adicionar")
        mp = p.get("min_probability")
        if mp is not None and not (isinstance(mp, (int, float)) and 0 <= mp <= 1):
            add(nid, "a confiança mínima deve estar entre 0 e 1")
        if kind != "action" and "then" in p:
            add(nid, "só ações usam “then”; use as respostas (ramos)")
    for e in graph.get("edges") or []:
        if e.get("from") not in seen:
            add(e.get("from"), "ligação saindo de um nó que não existe")
        if e.get("to") not in seen:
            add(e.get("from"), f"a resposta “{e.get('label')}” aponta para um nó que não existe (“{e.get('to')}”)")
        fk = next((n for n in graph.get("nodes") or [] if n.get("id") == e.get("from")), None)
        if fk and fk.get("kind") == "action" and e.get("label") != "then":
            add(e["from"], "ações só têm a saída “depois”")
        if fk and fk.get("kind") == "action" and (fk.get("props") or {}).get("action") == "stop":
            add(e["from"], "“parar” encerra a árvore: não pode ter próximo passo")
    if ids and graph.get("start") not in seen:
        add(None, "escolha o nó de início")
    elif ids:                                            # alcançáveis
        reach, q = {graph["start"]}, [graph["start"]]
        while q:
            c = q.pop()
            for e in graph.get("edges") or []:
                if e.get("from") == c and e.get("to") in seen and e["to"] not in reach:
                    reach.add(e["to"]); q.append(e["to"])
        for nid in ids:
            if nid not in reach:
                add(nid, "este nó nunca é alcançado a partir do início", "aviso")
    if not [e for e in errs if e["level"] == "erro"]:
        try:
            validate([tree if tree is not None else graph_to_tree(graph, check=False)])
        except TreeError as e:
            msg = str(e)
            nid = msg.split(":", 1)[0].split(".", 1)[1] if "." in msg.split(":", 1)[0] else None
            add(nid, msg.split(": ", 1)[-1])
        except Exception as e:
            add(None, f"{type(e).__name__}: {e}")
    return errs


def check_graph(graph: dict) -> dict:
    """Para a rota: {"ok": bool, "errors": [...], "tree": árvore|None}."""
    allv = validate_graph(graph)
    errs = [e for e in allv if e["level"] == "erro"]
    tree = None if errs else graph_to_tree(graph, check=False)
    return {"ok": not errs, "errors": errs, "warnings": [e for e in allv if e["level"] != "erro"], "tree": tree}
