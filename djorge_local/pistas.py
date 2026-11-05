"""Pistas: o que o texto MOSTRA sobre a ferramenta certa (verbos, objetos, formas e campos reconhecidos), e a
decisão com guardrails que junta essas pistas com a probabilidade do DOFL.

    p = pistas("marca dentista dia 20 às 9h", regras)      # regras = reconhecer(texto)
    -> {"ferramentas": {"cards": 0.86, ...}, "sinais": [...], "faltando": [], "perguntar": [], "explicito": None,
        "campos": ["data", "hora"]}
    d = decidir({"cards": 0.7, "bilhete": 0.2}, p)          # ou decidir(None, p) sem o modelo
    -> {"tool": "cards", "confianca": 0.81, "motivo": "...", "alternativas_uteis": [], "perguntar": [],
        "decidido": True}
    dicas("blablá", p)  -> ["Tente: “marca dentista dia 20 às 9h”", ...]

Guardrails
  * regra explícita ganha: "#destino &texto&" = bilhete; "pesquisa na internet" = web; "abre o WhatsApp" = abrir;
    uma conta pura ("2+2*3") = calcular;
  * a ferramenta só é escolhida se o modelo e as pistas concordarem o suficiente; o modelo sozinho, sem nenhuma pista
    e sem muita certeza, não atropela uma ferramenta com pistas fortes;
  * ferramenta incompatível com os campos perde força (calcular sem número, bilhete sem destino nem verbo de envio);
  * empate entre duas ferramentas com pistas = pergunta com opções (chips), nunca chute;
  * sem pistas para nada = "nenhuma" com dicas acionáveis.
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional

from djorge.runtime.recognizers import Trecho, _sem_acento, atalho_envio, corpo_mensagem, reconhecer

FERRAMENTAS = ("calcular", "datas_horas", "proximas_tarefas", "cards", "listas", "bilhete", "buscar", "regra", "web",
               "abrir", "nenhuma")
NOME = {"calcular": "Calcular", "datas_horas": "Datas e horas", "proximas_tarefas": "Agenda", "cards": "Cards",
        "listas": "Listas", "bilhete": "Mensagens", "buscar": "Busca", "regra": "Regras", "web": "Internet",
        "abrir": "Abrir app", "nenhuma": "Conversa", "interface": "Aparência"}
ROTULO = {"calcular": "Fazer a conta", "datas_horas": "Ver data ou hora", "proximas_tarefas": "Ver a agenda",
          "cards": "Criar um card", "listas": "Pôr numa lista", "bilhete": "Mandar mensagem",
          "buscar": "Buscar nos meus dados", "regra": "Criar uma regra", "web": "Buscar na internet",
          "abrir": "Abrir o app", "nenhuma": "Nada disso"}

# o mesmo rótulo como continuação de "Quer que eu …?" (subjuntivo): "crie um card ou mande uma mensagem"
FALA = {"calcular": "faça a conta", "datas_horas": "veja a data ou a hora", "proximas_tarefas": "mostre a agenda",
        "cards": "crie um card", "listas": "ponha numa lista", "bilhete": "mande uma mensagem",
        "buscar": "busque nos seus dados", "regra": "crie uma regra", "web": "busque na internet",
        "abrir": "abra o app", "nenhuma": "não faça nada"}

# ferramenta: [(nome do sinal, regex sem acento e minúsculo, peso)]
SINAIS = {
    "calcular": [
        ("verbo de conta", r"\b(quanto e|quanto da|quanto fica|quanto sai|calcul\w*|soma\w*|some|multiplic\w*|divid\w*|"
                           r"subtrai\w*|porcento|por cento|raiz quadrada|media de|converte\w*|em reais|em dolares)\b", 0.5),
        ("operação", r"\d\s*(?:[-+*x×÷^]|vezes|mais|menos|dividido por)\s*\d", 0.6),
        ("percentual de", r"\d\s*(?:%|por cento)\s*(?:de|do|da|sobre)\s*\d", 0.75),
    ],
    "datas_horas": [
        ("pergunta de hora", r"\b(que horas|horas sao|que hora e)\b", 0.85),
        ("pergunta de data", r"\b(que dia (e|cai|sera|vai ser)|qual (a|e a) data|data de hoje|dia da semana|hoje e que dia|"
                             r"em que dia|que dia da semana)\b", 0.8),
        ("contagem de dias", r"\b(quantos dias|quantas semanas|faltam? quant|quanto tempo (falta|ate)|ha quantos dias|"
                             r"desde quando)\b", 0.75),
        ("fuso", r"\b(fuso|horario de (brasilia|londres|nova york|lisboa|toquio|paris))\b", 0.7),
    ],
    "proximas_tarefas": [
        ("consulta da agenda", r"\b(o que (eu )?tenho|minha agenda|meus compromissos|minhas tarefas|o que vem (ai|agora|depois)|"
                               r"tenho (algo|alguma|algum)|como (esta|ta) (a |minha )?(agenda|semana|dia)|agenda de (hoje|amanha)|"
                               r"o que (esta|ta) marcado|quais (sao )?(os )?(meus )?(compromissos|lembretes|eventos))\b", 0.8),
        ("próximos", r"\bproxim[oa]s? (compromisso|tarefa|evento|reuniao|lembrete|consulta)s?\b", 0.7),
    ],
    "cards": [
        ("lembrete", r"\b(me lembr\w*|lembr[ae]\w*|lembrete|nao (me )?deixa esquecer|alarme|despertador)\b", 0.6),
        ("agendar", r"\b(marca|marque|marcar|agenda|agende|agendar|remarca\w*|desmarca\w*|adia\w*)\b", 0.55),
        ("anotar", r"\b(anota|anote|anotar|nota)\b", 0.5),
        ("compromisso", r"\b(reuniao|consulta|dentista|medico|medica|exame|compromisso|evento|aniversario|entrevista|"
                        r"call|aula|voo|viagem|jantar|almoco com|festa|prova|tarefa|card|prazo|pagar|vencimento)\b", 0.35),
        ("criar", r"\b(cria|crie|criar|adiciona|adicione|coloca|coloque|bota|poe|preciso|tenho que|nao esquecer)\b", 0.3),
    ],
    "listas": [
        ("lista", r"\blistas?\b", 0.75),
        ("itens de compra", r"\b(compras|mercado|supermercado|feira)\b", 0.25),
    ],
    "bilhete": [
        ("verbo de envio", r"\b(manda|mande|mandar|envia|envie|enviar|avisa|avise|escreve|escreva|responde|responda|"
                           r"fala (pra|pro|com)|diz (pra|pro)|diga (pra|pro)|pergunta (pra|pro)|comunica|informa)\b", 0.45),
        ("canal", r"\b(mensagem|e-?mail|zap|whats(app)?|sms|recado|bilhete)\b", 0.3),
        ("destino", r"\b(pra|para|pro|ao|à)\s+(o |a )?[a-z]", 0.15),
    ],
    "buscar": [
        ("verbo de busca", r"\b(procura|procure|procurar|busca|busque|buscar|acha|ache|achar|encontra|encontre|pesquisa|"
                           r"pesquise|localiza|localize)\b", 0.65),
        ("onde está", r"\b(onde (esta|ta|guardei|coloquei)|cade)\b", 0.6),
        ("meus dados", r"\b(nos meus|nas minhas|meus e-?mails|minhas mensagens|no historico)\b", 0.35),
    ],
    "regra": [
        ("gatilho", r"\b(sempre que|toda vez que|quando (chegar|receber|vier|aparecer)|se (eu )?receber|se chegar|"
                    r"automaticamente|regra|automacao|automatiza\w*)\b", 0.8),
    ],
    "web": [
        ("internet", r"\b(na internet|na web|no google|na net|online|em sites?|no site)\b", 0.85),
        ("pergunta de mundo", r"\b(quem (e|foi|era|ganhou|inventou|descobriu|escreveu)|o que (e|significa) (um|uma|o|a)?\s?\w|noticias?|cotacao|"
                              r"previsao do tempo|resultado do jogo|wikipedia)\b", 0.45),
    ],
    "abrir": [
        ("abrir app", r"\b(abre|abra|abrir|abri)\s+(o |a |meu |minha )?(whats(app)?|zap|navegador|chrome|edge|firefox|site|"
                      r"e-?mail|gmail|outlook|youtube|google|link|pagina|spotify|maps|mapa)\b", 0.9),
        ("abrir endereço", r"\b(abre|abra|abrir|acessa|acesse|entra em|entre em|vai (para|pro|no))\b", 0.35),
    ],
}
_RX = {t: [(n, re.compile(rx), w) for n, rx, w in ss] for t, ss in SINAIS.items()}
CONTA_PURA = re.compile(r"^[\d\s.,()+\-*/x×÷^%=?]+$")
WEB_EXPLICITO = re.compile(r"\b(pesquis\w*|busc\w*|procur\w*|ve|veja|olha|olhe)\b.{0,30}\b(na internet|na web|no google|na net|online)\b"
                           r"|\b(na internet|na web|no google)\b")
ABRIR_EXPLICITO = re.compile(r"^\s*(?:por favor\s+)?(?:me\s+)?(abre|abra|abrir|abri)\b")


def _une(ps: Iterable[float]) -> float:
    r = 1.0
    for p in ps:
        r *= 1 - max(0.0, min(1.0, p))
    return round(1 - r, 4)


def _cobre(texto: str, regras: List[Trecho], campos=("data", "hora", "telefone", "valor", "percentual", "cep", "cpf",
                                                      "cnpj")) -> str:
    """O texto com os trechos de data/hora/telefone... apagados (para '15/11' não parecer uma divisão)."""
    t = list(texto)
    for r in regras:
        if r.campo in campos and not (r.campo in ("valor", "percentual")):
            for i in range(r.ini, min(r.fim, len(t))):
                t[i] = " "
    return "".join(t)


def _destino(texto: str, regras: List[Trecho]) -> bool:
    if any(r.campo in ("endereço de e-mail", "telefone", "entidade") for r in regras):
        return True
    return bool(re.search(r"\b(?:pra|para|pro|ao|à|com)\s+(?:o\s+|a\s+)?[A-ZÀ-Ý][\wÀ-ÿ]+", texto))


def pistas(texto: str, regras: Optional[List[Trecho]] = None, contexto: Optional[Dict] = None) -> Dict:
    texto = str(texto or "")
    regras = reconhecer(texto) if regras is None else regras
    s = _sem_acento(texto.casefold())
    sem_datas = _sem_acento(_cobre(texto, regras).casefold())
    campos = sorted({r.campo for r in regras})
    sinais, por = [], {t: [] for t in FERRAMENTAS}
    for tool, ss in _RX.items():
        alvo = sem_datas if tool == "calcular" else s
        for nome, rx, w in ss:
            m = rx.search(alvo)
            if m:
                sinais.append({"tool": tool, "sinal": nome, "texto": m.group(0).strip(), "peso": w})
                por[tool].append(w)
    tem = lambda c: any(r.campo == c and r.valor for r in regras)
    quando = tem("data") or tem("hora") or tem("período do dia") or tem("frequência ou repetição")
    # campos reconhecidos também são pistas
    if quando:
        por["cards"].append(0.35)
        sinais.append({"tool": "cards", "sinal": "data ou hora", "peso": 0.35})
        if tem("frequência ou repetição"):
            por["cards"].append(0.3)
            sinais.append({"tool": "cards", "sinal": "repetição", "peso": 0.3})
    if (tem("endereço de e-mail") or tem("telefone")) and por["bilhete"]:
        por["bilhete"].append(0.4)
        sinais.append({"tool": "bilhete", "sinal": "contato", "peso": 0.4})
    if tem("url") and por["abrir"]:
        por["abrir"].append(0.5)
    if por["bilhete"] and _destino(texto, regras) and any(x["sinal"] == "verbo de envio" for x in sinais):
        por["bilhete"].append(0.35)
    corpo = corpo_mensagem(texto)
    if corpo and por["bilhete"]:
        por["bilhete"].append(0.2)
    if "?" in texto and quando and not por["cards"][:-1]:
        por["proximas_tarefas"].append(0.25)                 # "tenho algo amanhã?" é consulta, não card novo
    # explícitos
    explicito = None
    at = atalho_envio(texto) if "#" in texto else None
    if at and at.get("destino") and ("&" in texto or at.get("corpo")):
        explicito = "bilhete"
    elif CONTA_PURA.match(texto.strip()) and re.search(r"\d\s*[-+*/x×÷^%]\s*\d|\d\s*%", texto):
        explicito = "calcular"
    elif ABRIR_EXPLICITO.search(s) and (por["abrir"] and max(por["abrir"]) >= 0.9 or tem("url")):
        explicito = "abrir"
    elif WEB_EXPLICITO.search(s):
        explicito = "web"
    elif por["regra"]:
        explicito = None
    ferr = {t: _une(ws) for t, ws in por.items() if ws}
    # quem tem pista de busca + internet é web; consulta de agenda não é card novo
    if "web" in ferr and "buscar" in ferr and ferr["web"] >= 0.8:
        ferr["buscar"] = round(ferr["buscar"] * 0.4, 4)
    if "proximas_tarefas" in ferr and ferr["proximas_tarefas"] >= 0.7 and "cards" in ferr:
        ferr["cards"] = round(ferr["cards"] * 0.5, 4)
    if "listas" in ferr and ferr["listas"] >= 0.75 and "cards" in ferr:
        ferr["cards"] = round(ferr["cards"] * 0.5, 4)
    if "regra" in ferr and "cards" in ferr:
        ferr["cards"] = round(ferr["cards"] * 0.5, 4)
    if "datas_horas" in ferr and ferr["datas_horas"] >= 0.75:
        for t in ("cards", "calcular"):
            if t in ferr:
                ferr[t] = round(ferr[t] * 0.5, 4)
    if "abrir" in ferr and ferr["abrir"] >= 0.9:
        for t in ("bilhete", "buscar", "web"):
            if t in ferr:
                ferr[t] = round(ferr[t] * 0.6, 4)
    faltando = []
    if ferr.get("bilhete", 0) >= 0.4 and explicito != "abrir":
        if not (at and at.get("destino")) and not _destino(texto, regras):
            faltando.append("destino")
        if not (at and at.get("corpo")) and not corpo:
            faltando.append("texto")
    if ferr.get("calcular", 0) >= 0.4 and not re.search(r"\d", texto):
        faltando.append("números")
    ctx_tool = (contexto or {}).get("tool")
    if ctx_tool in FERRAMENTAS:
        explicito = ctx_tool
    return {"ferramentas": dict(sorted(ferr.items(), key=lambda x: -x[1])), "sinais": sinais, "faltando": faltando,
            "perguntar": [], "explicito": explicito, "campos": campos}


def compativel(tool: str, p: Dict, texto: str = "") -> float:
    """Fator 0..1: quanto os campos e as formas do texto combinam com a ferramenta."""
    falt = set(p.get("faltando") or [])
    sin = {x["sinal"] for x in p.get("sinais") or [] if x["tool"] == tool}
    if tool == "calcular":
        return 0.15 if "números" in falt or not re.search(r"\d", texto or "1") else 1.0
    if tool == "bilhete":
        return 0.4 if not sin & {"verbo de envio", "canal"} else 0.8 if "destino" in falt else 1.0
    if tool == "listas":
        return 1.0 if "lista" in sin else 0.6
    if tool == "regra":
        return 1.0 if sin else 0.5
    if tool == "abrir":
        return 1.0 if sin else 0.4
    if tool == "web":
        return 1.0 if sin else 0.7
    return 1.0


def _probs(model_probs) -> Dict[str, float]:
    if not model_probs:
        return {}
    if isinstance(model_probs, dict):
        return {str(k): float(v) for k, v in model_probs.items()}
    return {str(x["tool"]): float(x["p"]) for x in model_probs if isinstance(x, dict) and "tool" in x}


def pergunta(opcoes: List[str], texto: str = "O que você quer que eu faça?") -> Dict:
    return {"texto": texto, "opcoes": [{"rotulo": ROTULO.get(t, NOME.get(t, t)),
                                       "fala": FALA.get(t, ROTULO.get(t, NOME.get(t, t)).lower()), "patch": {"tool": t}} for t in opcoes]}


def decidir(model_probs, p: Dict, limiar: float = 0.3, margem: float = 0.12, texto: str = "") -> Dict:
    """Junta o modelo (probabilidades das ferramentas, ou None) com as pistas. Devolve
    {tool, confianca, motivo, alternativas_uteis, perguntar, decidido}."""
    pist = dict(p.get("ferramentas") or {})
    m = _probs(model_probs)
    if p.get("explicito"):
        t = p["explicito"]
        return {"tool": t, "confianca": 0.99, "motivo": "forma explícita no pedido", "alternativas_uteis": [],
                "perguntar": [], "decidido": True}
    comb = {}
    for t in FERRAMENTAS:
        if t == "nenhuma":
            continue
        pt, mt = pist.get(t, 0.0), m.get(t)
        if mt is None:                                   # sem modelo, ou ferramenta nova (web, abrir)
            v = pt if not m else pt * 0.9
        else:
            v = 0.6 * mt + 0.4 * pt
            if pt == 0 and mt < 0.85:                    # o modelo sozinho, sem pista nenhuma, vale menos
                v = mt * 0.5
        v *= compativel(t, p, texto)
        if v > 0:
            comb[t] = round(v, 4)
    if m.get("nenhuma", 0) >= 0.6 and max(pist.values() or [0]) < 0.5:
        return {"tool": "nenhuma", "confianca": round(m["nenhuma"], 4), "motivo": "o modelo não vê ferramenta para isto",
                "alternativas_uteis": [], "perguntar": [], "decidido": True}
    ordem = sorted(comb.items(), key=lambda x: -x[1])
    if not ordem or ordem[0][1] < limiar:
        alt = [t for t, v in ordem[:3] if v >= 0.15]
        return {"tool": "nenhuma", "confianca": round(1 - (ordem[0][1] if ordem else 0), 4),
                "motivo": "sem pistas suficientes", "alternativas_uteis": alt,
                "perguntar": [pergunta(alt)] if len(alt) >= 2 else [], "decidido": True}
    top, v = ordem[0]
    if len(ordem) > 1 and ordem[1][1] >= limiar and v - ordem[1][1] < margem:
        alt = [t for t, x in ordem[:3] if x >= limiar and v - x < margem * 2]
        return {"tool": top, "confianca": round(v, 4), "motivo": "duas ferramentas servem", "alternativas_uteis": alt,
                "perguntar": [pergunta(alt)], "decidido": False}
    motivo = "modelo e pistas concordam" if m and pist.get(top) else "pelas pistas" if not m else "pelo modelo"
    return {"tool": top, "confianca": round(min(0.99, v), 4), "motivo": motivo, "alternativas_uteis": [],
            "perguntar": [], "decidido": True}


# ferramentas que o DOFL não conhece (vieram depois do treino): o modelo as vê como "buscar"/"nenhuma"; as pistas fortes
# (ex.: "na internet", "abre o WhatsApp") levam a escolha para a ferramenta nova.
NOVAS = {"web": (("buscar", "nenhuma"), 0.8), "abrir": (("buscar", "nenhuma", "bilhete"), 0.9)}


def decidir_modelo(model_probs, p: Dict, texto: str = "", can: Optional[float] = None, seguro: float = 0.35,
                   margem: float = 0.1) -> Dict:
    """O MODELO decide (prioridade "modelo"): a ferramenta é a que o DOFL escolheu, com as pistas só como
    confirmação e como guarda-corpo (campos incompatíveis, ferramentas novas que o modelo não conhece). Quando o
    modelo está inseguro (< `seguro`), cai em decidir() (modelo + pistas). Devolve o mesmo formato de decidir() e
    "por": "modelo" | "regras" (quem de fato escolheu)."""
    m = _probs(model_probs)
    if not m:
        return dict(decidir(None, p, texto=texto), por="regras")
    if p.get("explicito"):
        return dict(decidir(m, p, texto=texto), por="regras")
    pist = dict(p.get("ferramentas") or {})
    # guarda-corpo mais leve que em decidir(): só a conta sem número cai de vez; o resto perde no máximo metade
    fator = lambda t: 1.0 if t == "nenhuma" else compativel(t, p, texto) if t == "calcular" else \
        0.5 + 0.5 * compativel(t, p, texto)
    adj = {t: round(v * fator(t), 4) for t, v in m.items() if t in FERRAMENTAS}
    for novo, (bases, lim) in NOVAS.items():
        if pist.get(novo, 0) >= lim:
            adj[novo] = round(max([adj.get(b, 0.0) for b in bases] + [0.0]) + 0.01, 4)
            for b in bases:
                if b in adj and b != "nenhuma":
                    adj[b] = round(adj[b] * 0.5, 4)
            adj["nenhuma"] = round(adj.get("nenhuma", 0.0) * 0.5, 4)
    ordem = sorted(adj.items(), key=lambda x: -x[1])
    if not ordem:
        return dict(decidir(None, p, texto=texto), por="regras")
    top, v = ordem[0]
    if can is not None and can < 0.2 and top != "nenhuma" and pist.get(top, 0) < 0.5:
        top, v = "nenhuma", max(v, 1 - can)
    if v < seguro:                                        # o modelo não tem certeza: modelo + pistas juntos
        d = decidir(m, p, texto=texto)
        return dict(d, por="modelo" if d["tool"] == top else "regras")
    if top == "nenhuma":
        alt = [t for t, x in sorted(pist.items(), key=lambda x: -x[1]) if x >= 0.45 and t != "nenhuma"][:3]
        return {"tool": "nenhuma", "confianca": round(v, 4), "motivo": "o modelo não vê ferramenta para isto",
                "alternativas_uteis": alt, "perguntar": [pergunta(alt)] if len(alt) >= 2 else [], "decidido": True,
                "por": "modelo"}
    if len(ordem) > 1 and ordem[1][0] != "nenhuma" and ordem[1][1] >= 0.3 and v - ordem[1][1] < margem \
            and pist.get(ordem[1][0], 0) > pist.get(top, 0):
        alt = [top, ordem[1][0]]
        return {"tool": top, "confianca": round(v, 4), "motivo": "duas ferramentas servem", "alternativas_uteis": alt,
                "perguntar": [pergunta(alt)], "decidido": False, "por": "modelo"}
    motivo = "o modelo decidiu e as pistas concordam" if pist.get(top) else "o modelo decidiu"
    return {"tool": top, "confianca": round(min(0.99, v), 4), "motivo": motivo, "alternativas_uteis": [],
            "perguntar": [], "decidido": True, "por": "modelo"}


# ------------------------------------------------------------------ dicas --
EXEMPLOS = {
    "cards": ["marca dentista dia 20 às 9h", "me lembra de pagar a luz todo dia 10", "reunião com a Ana sexta das 14h às 16h"],
    "listas": ["adiciona leite e café na lista de compras", "cria a lista da viagem com protetor e chinelo"],
    "bilhete": ["manda pra Ana dizendo que chego às 10h", "#ana@email.com &chego às 10h&"],
    "calcular": ["quanto é 18% de 2450", "1250 dividido por 3"],
    "datas_horas": ["que dia cai 15/11", "quantos dias faltam para o Natal"],
    "proximas_tarefas": ["o que tenho amanhã?", "como está minha semana?"],
    "buscar": ["procura o boleto da escola", "acha o e-mail da Ana sobre orçamento"],
    "web": ["pesquisa na internet a previsão do tempo", "busca no google horário da farmácia"],
    "abrir": ["abre o WhatsApp da Ana com “tô chegando”", "abre o site gov.br"],
    "regra": ["sempre que chegar boleto, cria uma tarefa", "quando receber e-mail do chefe, me avisa"],
}
_VERBOS = re.compile(r"(?i)^\s*(?:me\s+)?(?:lembra(?:r)?|lembre|marca(?:r)?|marque|agenda(?:r)?|cria(?:r)?|crie|anota(?:r)?|"
                     r"manda(?:r)?|mande|procura(?:r)?|busca(?:r)?|abre|abrir|pesquisa(?:r)?)\s+(?:de\s+|que\s+|o\s+|a\s+)?")


def dicas(texto: str, p: Optional[Dict] = None, tool: Optional[str] = None, n: int = 3) -> List[str]:
    """2-3 sugestões 'Tente: “…”' adequadas ao que a pessoa escreveu."""
    p = p or pistas(texto)
    s = " ".join(str(texto or "").split()).strip(" .!?")
    nucleo = _VERBOS.sub("", s)[:60].strip()
    falt = set(p.get("faltando") or [])
    campos = set(p.get("campos") or [])
    ordem = [tool] if tool and tool in EXEMPLOS else []
    ordem += [t for t in (p.get("ferramentas") or {}) if t in EXEMPLOS and t not in ordem]
    out: List[str] = []
    alvo = ordem[0] if ordem else None
    curto = nucleo and len(nucleo.split()) <= 3 and not re.search(
        r"(?i)\b(me|mensagem|e-?mail|zap|whatsapp|uma|um|isso|algo|coisa|conta|piada)\b", nucleo)
    if alvo == "bilhete" and "destino" in falt:
        out.append(f"manda pra Ana dizendo que {nucleo}" if curto else "manda pra Ana dizendo que chego às 10h")
    elif alvo == "cards" and curto and not campos & {"data", "hora", "frequência ou repetição"}:
        out.append(f"{nucleo} amanhã às 9h")
        out.append(f"me lembra de {nucleo} todo dia 20")
    elif alvo == "web" and nucleo:
        out.append(f"pesquisa na internet {nucleo}")
    elif alvo == "calcular" and "números" in falt:
        out.append("quanto é 18% de 2450")
    elif alvo is None and curto and not re.search(r"\?$", str(texto).strip()):
        out.append(f"me lembra de {nucleo} amanhã às 9h")
        out.append(f"pesquisa na internet {nucleo}")
    for t in ordem + [x for x in ("cards", "bilhete", "proximas_tarefas") if x not in ordem]:
        for e in EXEMPLOS[t]:
            if len(out) >= n:
                break
            if e not in out:
                out.append(e)
        if len(out) >= n:
            break
    return [f"Tente: “{x}”" for x in out[:n]]
