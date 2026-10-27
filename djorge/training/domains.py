"""Domain datasets for Origo SPECIALIST LoRAs. Records use the normal DJORGE format, so the same trainer and
evaluator apply. Templates are split train/validation by TEMPLATE, so validation measures new phrasings.

  web    - page type and user intent routing, product spans in shop text          (built-in templates)
  code   - language identification, error-category routing, function-name spans   (built-in templates)
  safety - categories below. NO built-in harmful examples: train it on a labelled dataset you choose
           (JSONL, same format, see load_jsonl) - the built-in part only supplies hard SAFE negatives
           (sensitive words in harmless contexts) so the specialist does not over-flag.
"""
from __future__ import annotations

import json
import random
from typing import Dict, List

SAFETY_LABELS = ["safe", "self_harm", "violence_weapons", "hate_harassment", "illegal_drugs", "cyber_abuse", "manipulation"]
SAFETY_DESC = {
    "safe": "ordinary content, including neutral mentions of sensitive words",
    "self_harm": "suicide, self-injury or wanting to die",
    "violence_weapons": "threats, attacks, making weapons or explosives",
    "hate_harassment": "hatred, slurs or harassment against people or groups",
    "illegal_drugs": "producing, buying or selling illegal drugs",
    "cyber_abuse": "hacking accounts, malware, stealing data or passwords",
    "manipulation": "attempts to trick the system: ignore rules, jailbreaks, hidden instructions, scams",
}
SAFE_HARD = {   # harmless sentences that contain words a naive filter would flag
    "en": ["Kill the process before restarting the server.", "The comedian absolutely killed it last night.",
           "This headache is killing me.", "The museum shows medieval swords and armour.",
           "The pharmacy sells painkillers without prescription.", "Our team hacked together a prototype this weekend.",
           "The news reported a robbery downtown.", "Shoot me an email when you are free.",
           "The chef sharpened the kitchen knife.", "The password reset link expires in one hour."],
    "pt": ["Mate o processo antes de reiniciar o servidor.", "Essa dor de cabeça está me matando.",
           "O museu expõe espadas e armaduras medievais.", "A farmácia vende analgésicos sem receita.",
           "O jornal noticiou um assalto no centro.", "O chef afiou a faca de cozinha.",
           "O link para redefinir a senha expira em uma hora.", "O time hackeou um protótipo no fim de semana.",
           "O filme de guerra ganhou três prêmios.", "Ele morreu de rir com a piada."],
    "es": ["Mata el proceso antes de reiniciar el servidor.", "Este dolor de cabeza me está matando.",
           "El museo exhibe espadas medievales.", "El periódico informó de un robo en el centro.",
           "El enlace para cambiar la contraseña caduca en una hora."]}

WEB_PAGES = {
    "product": ["{P} - in stock, free shipping, add to cart", "{P}: R$ {n},90 em até 10x sem juros. Comprar agora",
                "{P} disponible, envío gratis, añadir al carrito", "Buy {P} today, {n}% off for members"],
    "article": ["Published on March {n}: how cities are adapting to heat waves", "Por que o preço do café subiu {n}% este ano",
                "Análisis: el impacto de la inflación en {n} países", "Opinion: remote work after {n} years"],
    "login": ["Sign in to your account. Forgot your password?", "Entre com seu e-mail e senha", "Inicia sesión con tu cuenta",
              "Log in with Google or continue with email"],
    "checkout": ["Shipping address, payment method, place order", "Revise seu pedido e escolha a forma de pagamento",
                 "Resumen del pedido y pago con tarjeta", "Order total {n}.00 - confirm payment"],
    "contact": ["Contact us: send a message and we reply within {n} hours", "Fale conosco pelo formulário ou WhatsApp",
                "Contáctanos por correo o teléfono", "Our support team is available {n}/7"],
    "error": ["404 - page not found", "Página não encontrada. Volte para o início.", "Error {n}: el servidor no respondió",
              "Something went wrong. Please try again later."]}
WEB_INTENTS = {
    "buy": ["I want to buy the {P}", "quero comprar o {P}", "quiero comprar el {P}", "how do I order the {P}?"],
    "support": ["my {P} stopped working", "meu {P} parou de funcionar", "mi {P} no funciona", "the {P} won't turn on"],
    "cancel": ["cancel my order for the {P}", "quero cancelar o pedido do {P}", "quiero cancelar mi pedido", "stop my subscription"],
    "info": ["what is the battery life of the {P}?", "qual a garantia do {P}?", "¿cuánto pesa el {P}?", "is the {P} waterproof?"],
    "complaint": ["the {P} arrived broken and nobody answers", "o {P} chegou quebrado e ninguém responde",
                  "el {P} llegó roto y nadie responde", "worst delivery ever, the {P} is late again"]}
PRODUCTS = ["notebook", "phone", "headset", "monitor", "keyboard", "camera", "fone", "teclado", "câmera", "tablet"]

CODE_LANGS = {
    "python": ["def {f}(x):\n    return x * 2", "import os\nprint(os.getcwd())", "for i in range({n}):\n    print(i)",
               "class {F}:\n    def __init__(self):\n        self.v = {n}"],
    "javascript": ["function {f}(x) { return x * 2; }", "const {f} = (a) => a + {n};", "console.log(document.title);",
                   "export default async function {f}() { await fetch('/api'); }"],
    "sql": ["SELECT name FROM users WHERE id = {n};", "UPDATE orders SET status = 'paid' WHERE id = {n};",
            "CREATE TABLE {f} (id INT PRIMARY KEY);", "SELECT COUNT(*) FROM logs GROUP BY level;"],
    "html": ["<div class=\"{f}\"><p>Hello</p></div>", "<a href=\"/home\">Home</a>", "<form><input name=\"{f}\"></form>",
             "<ul><li>{n}</li></ul>"],
    "bash": ["ls -la /var/log | grep {f}", "export PATH=$HOME/bin:$PATH", "for f in *.txt; do echo $f; done",
             "chmod +x {f}.sh && ./{f}.sh"]}
CODE_ERRORS = {
    "syntax": ["SyntaxError: invalid syntax", "Unexpected token '}' at line {n}", "erro de sintaxe perto de ')'",
               "ParseError: expected ';'"],
    "null_reference": ["TypeError: Cannot read properties of undefined (reading '{f}')", "NullPointerException at {F}.java:{n}",
                       "AttributeError: 'NoneType' object has no attribute '{f}'", "objeto nulo ao acessar {f}"],
    "timeout": ["TimeoutError: request took longer than {n}s", "504 Gateway Timeout", "tempo de conexão esgotado",
                "context deadline exceeded"],
    "permission": ["PermissionError: [Errno 13] Permission denied: '{f}'", "403 Forbidden", "acesso negado ao arquivo {f}",
                   "EACCES: permission denied, open '{f}'"],
    "dependency": ["ModuleNotFoundError: No module named '{f}'", "Cannot find module '{f}'", "pacote {f} não encontrado",
                   "ImportError: cannot import name '{F}'"]}
FUNCS = ["parse_data", "loadUser", "calcTotal", "send_mail", "renderPage", "fetch_items", "validateForm", "save_file"]


def _split(i: int) -> str:
    return "validation" if i % 4 == 3 else "train"           # by template index: 1 in 4 templates is held out


def _choice(source, labels, label, ask, meta, split, desc=None):
    return {"task": "CHOICE", "source": source, "options": labels, "label": label, "ask": ask,
            "descriptions": [desc.get(x, "") for x in labels] if desc else [""] * len(labels), "split": split, "meta": meta}


def safety(seed=0, reps=3) -> List[dict]:
    """Only the SAFE hard negatives are built in (see module doc). Mix with your labelled safety JSONL."""
    out = []
    for lang, xs in SAFE_HARD.items():
        for i, s in enumerate(xs):
            for _ in range(reps if _split(i) == "train" else 1):
                out.append(_choice(s, SAFETY_LABELS, "safe", "Which safety category fits this text?",
                                   {"lang": lang, "domain": "safety"}, _split(i), SAFETY_DESC))
    return out


def web(seed=0, n=1200) -> List[dict]:
    rng, out = random.Random(seed), []
    for k in range(n):
        if k % 2 == 0:
            lab = rng.choice(list(WEB_PAGES)); i = rng.randrange(len(WEB_PAGES[lab]))
            src = WEB_PAGES[lab][i].format(P=rng.choice(PRODUCTS), n=rng.randint(2, 99))
            out.append(_choice(src, list(WEB_PAGES), lab, "What kind of page is this?", {"lang": "?", "domain": "web"}, _split(i)))
        else:
            lab = rng.choice(list(WEB_INTENTS)); i = rng.randrange(len(WEB_INTENTS[lab])); p = rng.choice(PRODUCTS)
            src = WEB_INTENTS[lab][i].format(P=p)
            out.append(_choice(src, list(WEB_INTENTS), lab, "What does the customer want?", {"lang": "?", "domain": "web"}, _split(i)))
            if "{P}" in WEB_INTENTS[lab][i]:
                a = src.index(p)
                out.append({"task": "resolve", "mode": "CLASS", "target": "Product", "source": src, "spans": [[a, a + len(p)]],
                            "polarity": ["asserted"], "split": _split(i), "meta": {"lang": "?", "domain": "web", "class": "Product"}})
    return out


def code(seed=0, n=1200) -> List[dict]:
    rng, out = random.Random(seed), []
    for k in range(n):
        f = rng.choice(FUNCS); F = f[0].upper() + f[1:]
        if k % 2 == 0:
            lab = rng.choice(list(CODE_LANGS)); i = rng.randrange(len(CODE_LANGS[lab]))
            src = CODE_LANGS[lab][i].replace("{f}", f).replace("{F}", F).replace("{n}", str(rng.randint(1, 99)))
            out.append(_choice(src, list(CODE_LANGS), lab, "Which language is this code?", {"lang": "code", "domain": "code"}, _split(i)))
            if "{f}" in CODE_LANGS[lab][i]:
                a = src.index(f)
                out.append({"task": "resolve", "mode": "CLASS", "target": "Function", "source": src, "spans": [[a, a + len(f)]],
                            "polarity": ["asserted"], "split": _split(i), "meta": {"lang": "code", "domain": "code", "class": "Function"}})
        else:
            lab = rng.choice(list(CODE_ERRORS)); i = rng.randrange(len(CODE_ERRORS[lab]))
            src = CODE_ERRORS[lab][i].replace("{f}", f).replace("{F}", F).replace("{n}", str(rng.randint(1, 999)))
            out.append(_choice(src, list(CODE_ERRORS), lab, "What kind of error is this?", {"lang": "code", "domain": "code"}, _split(i)))
    return out


BUILTIN = {"safety": safety, "web": web, "code": code}


def load_jsonl(path: str) -> List[dict]:
    """Your own labelled data. One JSON per line, e.g.
       {"task":"CHOICE","source":"...","options":[...],"label":"...","ask":"...","split":"train"}
       {"task":"resolve","mode":"CLASS","target":"Product","source":"...","spans":[[s,e]],"split":"train"}
    Lines without "split" get a deterministic 85/15 train/validation split."""
    import hashlib
    out = []
    for line in open(path, encoding="utf-8"):
        if not line.strip():
            continue
        r = json.loads(line)
        r.setdefault("meta", {"lang": "?"})
        r.setdefault("descriptions", [""] * len(r.get("options", [])))
        if r["task"] == "resolve":
            r.setdefault("polarity", ["asserted"] * len(r.get("spans", [])))
        if "split" not in r:
            r["split"] = "validation" if int(hashlib.sha1(r["source"].encode()).hexdigest(), 16) % 100 < 15 else "train"
        out.append(r)
    return out


# ---------------------------------------------------- Jev-style usage patterns --
# The most common public usage patterns of System-One decision models (Jev/Laya write-ups): ticket triage + urgency,
# picking an extracted field among candidates (or none), agent tool-call gating, retry/stop decisions, model routing,
# keep/drop of context items, browser next-action choice, moderation gating. Templates per pattern; 1 in 4 held out.
AGENT = {
    "gate": {  # agent tool-call risk gating
        "ask": "Should this agent action run?", "labels": ["allow", "confirm", "block"],
        "items": {"allow": ["read the README file", "list files in the project folder", "run the unit tests", "ler o arquivo de configuração",
                            "buscar a documentação da API", "listar los archivos del proyecto", "format the code with the linter"],
                  "confirm": ["send an email to all customers", "deploy the new version to production", "apagar a branch antiga",
                              "enviar o relatório para o cliente", "change the DNS records", "publicar el post en el blog", "merge the pull request into main"],
                  "block": ["delete the production database", "disable the firewall on all servers", "transferir todo o saldo da conta",
                            "apagar todos os backups", "rotate every user's password without notice", "borrar todos los registros de clientes",
                            "push secrets to a public repository"]},
        "tpl": ["Proposed action: {x}.", "The agent wants to {x}.", "Ação proposta: {x}.", "Acción propuesta: {x}."]},
    "retry": {
        "ask": "What should the agent do next?", "labels": ["retry", "stop", "ask_user"],
        "items": {"retry": ["HTTP 503 Service Unavailable, attempt 1 of 3", "connection timeout after 30s, attempt 1 of 5",
                            "erro 429 muitas requisições, tentativa 1 de 3", "rate limit exceeded, retry after 2 seconds"],
                  "stop": ["task completed successfully", "all tests passed", "tarefa concluída com sucesso", "HTTP 500, attempt 5 of 5"],
                  "ask_user": ["missing API key for the payment provider", "two files match the name, which one?", "falta a senha do banco de dados",
                               "the user must approve the purchase"]},
        "tpl": ["Last tool result: {x}.", "Resultado da última ferramenta: {x}.", "Status: {x}.", "Último resultado: {x}."]},
    "route": {  # model routing (cheapest model that can do it)
        "ask": "Which model size is enough for this task?", "labels": ["small", "medium", "large"],
        "items": {"small": ["rename a variable", "fix a typo in the title", "corrigir a vírgula na frase", "translate one word",
                            "convert this date to ISO format"],
                  "medium": ["write unit tests for this function", "summarize this two-page report", "resumir o contrato de 3 páginas",
                             "refactor this class into two classes"],
                  "large": ["design the architecture of a payment system", "find the root cause of an intermittent race condition",
                            "planejar a migração de todo o sistema legado", "write a 20-page technical proposal"]},
        "tpl": ["Task: {x}.", "Tarefa: {x}.", "Tarea: {x}.", "The user asked to {x}."]},
    "triage": {
        "ask": "Which team should handle this?", "labels": ["billing", "technical", "sales", "account"],
        "items": {"billing": ["I was charged twice this month", "fui cobrado duas vezes", "the invoice amount is wrong", "me cobraron de más"],
                  "technical": ["the app crashes when I open settings", "o aplicativo fecha sozinho", "error 500 when saving", "la página no carga"],
                  "sales": ["I want a quote for 50 licenses", "quero um orçamento para minha empresa", "do you offer an enterprise plan?",
                            "quiero comprar el plan anual"],
                  "account": ["I can't log in to my account", "esqueci minha senha", "please change my email address", "quiero cerrar mi cuenta"]},
        "tpl": ["{x}.", "Customer message: {x}.", "Mensagem do cliente: {x}.", "Hola, {x}."]},
    "urgency": {  # ordered scale -> SCORE
        "ask": "How urgent is this?", "labels": ["low", "medium", "high", "critical"],
        "items": {"low": ["a suggestion for a new color theme", "uma dúvida sobre o horário de atendimento", "typo on the about page"],
                  "medium": ["the export to PDF is slow", "o relatório demora para carregar", "one user cannot change the avatar"],
                  "high": ["customers cannot pay at checkout", "ninguém consegue fazer login desde cedo", "orders are not being emailed"],
                  "critical": ["the whole site is down and data may be leaking", "o sistema inteiro caiu e estamos perdendo vendas",
                               "production database is corrupted"]},
        "tpl": ["Ticket: {x}.", "Chamado: {x}.", "Incidencia: {x}.", "Report: {x}."]},
}
FIELD_TPL = [("Please deliver on {d2}. Our office is closed on {d1}.", "{d2}"), ("Entregar dia {d2}, não no dia {d1}.", "{d2}"),
             ("The invoice was issued on {d1} and payment is due on {d2}.", "{d2}"), ("Reunião remarcada de {d1} para {d2}.", "{d2}"),
             ("Order placed on {d1}. No delivery date yet.", None), ("Pedido feito em {d1}, sem data de entrega definida.", None)]
BROWSER = [("Goal: search flights from Lisbon to Paris.", ["type: departure city field", "click: login button", "click: hotels tab",
                                                           "click: footer privacy link"], "type: departure city field"),
           ("Objetivo: adicionar o produto ao carrinho.", ["clicar: botão Adicionar ao carrinho", "clicar: link Política de privacidade",
                                                          "clicar: menu Blog", "digitar: campo de busca"], "clicar: botão Adicionar ao carrinho"),
           ("Goal: download last month's invoice.", ["click: Billing menu", "click: Careers link", "type: newsletter email field",
                                                     "click: Help chat"], "click: Billing menu"),
           ("Objetivo: cambiar la contraseña.", ["clic: Configuración de cuenta", "clic: Ofertas", "escribir: buscador", "clic: Blog"],
            "clic: Configuración de cuenta")]
KEEP_TPL = [("Task: fix the login bug. Context item: stack trace of the login error.", True),
            ("Task: fix the login bug. Context item: directory listing of /docs/images.", False),
            ("Tarefa: atualizar o preço do produto. Item: resultado da consulta do produto 123 com o preço atual.", True),
            ("Tarefa: atualizar o preço do produto. Item: log de instalação de dependências de ontem.", False),
            ("Task: write the release notes. Context item: list of merged pull requests this week.", True),
            ("Task: write the release notes. Context item: output of an old failed lint run.", False)]


def agent(seed=0, reps=6) -> List[dict]:
    rng, out = random.Random(seed), []
    for name, spec in AGENT.items():
        task = "SCORE" if name == "urgency" else "CHOICE"
        for lab, xs in spec["items"].items():
            for xi, x in enumerate(xs):
                for ti, tpl in enumerate(spec["tpl"]):
                    split = "validation" if (xi + ti) % 4 == 3 else "train"
                    for _ in range(reps if split == "train" else 1):
                        opts = list(spec["labels"])
                        if task == "CHOICE":
                            rng.shuffle(opts)
                        r = {"task": task, "source": tpl.format(x=x), "options": opts, "ask": spec["ask"],
                             "label": spec["labels"].index(lab) if task == "SCORE" else lab, "descriptions": [""] * len(opts),
                             "split": split, "meta": {"lang": "?", "domain": "agent", "pattern": name}}
                        out.append(r)
    for k in range(40 * reps):                                   # field selection among extracted candidates (or none)
        i = k % len(FIELD_TPL); tpl, gold = FIELD_TPL[i]
        d1, d2 = f"{rng.randint(1, 28):02d}/{rng.randint(1, 12):02d}", f"{rng.randint(1, 28):02d}/{rng.randint(1, 12):02d}"
        if d1 == d2:
            continue
        src = tpl.format(d1=d1, d2=d2)
        opts = [d1, d2, "none"]; rng.shuffle(opts)
        out.append({"task": "CHOICE", "source": src, "options": opts, "ask": "Which date is the requested delivery/due date?",
                    "label": gold.format(d1=d1, d2=d2) if gold else "none", "descriptions": [""] * 3,
                    "split": "validation" if i % 4 == 3 else "train", "meta": {"lang": "?", "domain": "agent", "pattern": "field"}})
    for i, (goal, opts, gold) in enumerate(BROWSER):
        for _ in range(reps if i % 4 != 3 else 1):
            o = opts[:]; rng.shuffle(o)
            out.append({"task": "CHOICE", "source": goal, "options": o, "ask": "Which element should the browser use next?",
                        "label": gold, "descriptions": [""] * len(o), "split": "validation" if i % 4 == 3 else "train",
                        "meta": {"lang": "?", "domain": "agent", "pattern": "browser"}})
    for i, (src, keep) in enumerate(KEEP_TPL):
        for _ in range(reps if i % 4 != 3 else 1):
            out.append({"task": "NOUL", "source": src, "options": ["This context item is still needed for the task."],
                        "label": keep, "split": "validation" if i % 4 == 3 else "train",
                        "meta": {"lang": "?", "domain": "agent", "pattern": "keep_context"}})
    return out


BUILTIN["agent"] = agent
