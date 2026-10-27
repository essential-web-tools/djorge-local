"""Fixed questions of the Djorge Sentinel, shared by TRAINING and the API (identical text on both sides).

The Sentinel is an Origo-style text classifier: it receives plain text and answers a fixed question about it.
Every kind is trained only from PUBLIC labelled datasets (djorge/sentinel/public_br.py).

  texto          mensagem / SMS / chat                -> seguro | golpe | phishing | spam
  email          e-mail (assunto + corpo, como texto)  -> legítimo | phishing | spam
  dominio        domínio ou URL escrito como texto     -> legítimo | phishing | malicioso
  prompt         texto enviado a uma IA                -> seguro | injeção de prompt | jailbreak
  saida          texto produzido por uma IA            -> segura | insegura
  ofensa         comentário / post                     -> respeitoso | ofensivo
  desinformacao  notícia ou afirmação                  -> verdadeira | falsa
  dados          any text: personal-data passages are MARKED by the span head (no classification)
Every classified kind also gets the same danger SCORE (nenhum .. crítico).
Nothing is fetched or opened: a URL is only read as a string.
"""
from __future__ import annotations

KINDS = {
    "texto": {
        "ask": "Esta mensagem é segura ou uma ameaça?",
        "asks": ["Esta mensagem é segura ou uma ameaça?", "Que tipo de ameaça é esta, se houver?",
                 "Classifique o risco desta mensagem.", "Is this message safe or a threat?"],
        "options": ["seguro", "golpe", "phishing", "spam"],
        "descriptions": ["legítima, sem ameaça", "engana a pessoa para pagar ou transferir dinheiro",
                         "rouba senhas, códigos ou dados de cartão se passando por alguém",
                         "propaganda em massa não solicitada"],
        "safe": "seguro", "danger": {"seguro": 0, "spam": 1, "golpe": 3, "phishing": 3},
    },
    "email": {
        "ask": "Este e-mail é legítimo?",
        "asks": ["Este e-mail é legítimo?", "Este e-mail é phishing?", "Classifique este e-mail.",
                 "Is this e-mail legitimate?"],
        "options": ["legítimo", "phishing", "spam"],
        "descriptions": ["e-mail normal, sem golpe", "tenta roubar dados ou dinheiro se passando por alguém",
                         "propaganda em massa não solicitada"],
        "safe": "legítimo", "danger": {"legítimo": 0, "spam": 1, "phishing": 3},
    },
    "dominio": {
        "ask": "Este endereço é seguro para abrir?",
        "asks": ["Este endereço é seguro para abrir?", "Este link é confiável?", "Avalie este domínio.",
                 "Is this link safe to open?"],
        "options": ["legítimo", "phishing", "malicioso"],
        "descriptions": ["site comum, sem sinais de golpe", "imita um site para roubar dados",
                         "distribui programa malicioso ou foi invadido"],
        "safe": "legítimo", "danger": {"legítimo": 0, "phishing": 3, "malicioso": 4},
    },
    "prompt": {
        "ask": "Este texto enviado a uma IA tenta manipulá-la?",
        "asks": ["Este texto enviado a uma IA tenta manipulá-la?", "Este prompt é seguro?",
                 "Classifique este pedido feito a um assistente.", "Is this prompt to an AI safe?"],
        "options": ["seguro", "injeção de prompt", "jailbreak"],
        "descriptions": ["pedido normal a uma IA", "tenta trocar as instruções da IA, revelar regras ou vazar dados",
                         "tenta tirar as regras de segurança da IA"],
        "safe": "seguro", "danger": {"seguro": 0, "injeção de prompt": 3, "jailbreak": 3},
    },
    "saida": {
        "ask": "Esta resposta de uma IA é segura para mostrar?",
        "asks": ["Esta resposta de uma IA é segura para mostrar?", "Esta saída de modelo é insegura?",
                 "Classifique esta resposta de assistente.", "Is this AI response safe?"],
        "options": ["segura", "insegura"],
        "descriptions": ["resposta normal ou recusa", "resposta que ajuda a causar dano ou viola políticas de segurança"],
        "safe": "segura", "danger": {"segura": 0, "insegura": 3},
    },
    "ofensa": {
        "ask": "Este comentário é ofensivo?",
        "asks": ["Este comentário é ofensivo?", "Este texto tem discurso de ódio ou ofensa?",
                 "Classifique o tom deste comentário.", "Is this comment offensive?"],
        "options": ["respeitoso", "ofensivo"],
        "descriptions": ["sem ofensa", "ofende, insulta ou ataca pessoas ou grupos"],
        "safe": "respeitoso", "danger": {"respeitoso": 0, "ofensivo": 2},
    },
    "desinformacao": {
        "ask": "Este texto tem cara de notícia falsa?",
        "asks": ["Este texto tem cara de notícia falsa?", "Esta afirmação parece desinformação?",
                 "Classifique esta notícia.", "Does this look like fake news?"],
        "options": ["verdadeira", "falsa"],
        "descriptions": ["no estilo de veículos e checagens confiáveis", "no estilo de notícias falsas já checadas"],
        "safe": "verdadeira", "danger": {"verdadeira": 0, "falsa": 2},
    },
}

# span targets of the span head (trained from public token-level labels)
SPANS = {
    "dados": {"target": "dado pessoal",
              "definition": "nome, documento, telefone, e-mail, endereço, cartão, conta ou outro dado de uma pessoa"},
}

DANGER_ASK = "Qual o nível de perigo deste conteúdo?"
DANGER_LEVELS = ["nenhum", "baixo", "médio", "alto", "crítico"]
DANGER_DESC = ["nada de risco", "incômodo, sem dano real", "pode causar algum prejuízo",
               "provável golpe, ataque ou roubo de dados", "dano grave e imediato"]
DANGER_SCORE = [0, 25, 50, 75, 100]

PROFILES = {   # minimum danger level for "avisar" / "bloquear", and minimum probability to act
    "conservador": {"warn": 1, "block": 2, "min_p": 0.5},
    "equilibrado": {"warn": 2, "block": 3, "min_p": 0.6},
    "permissivo": {"warn": 3, "block": 4, "min_p": 0.7},
}


def action(kind: str, answer, danger_idx: int, prob: float, abstain: bool, profile: str = "equilibrado") -> str:
    p = PROFILES.get(profile, PROFILES["equilibrado"])
    if abstain or answer is None:
        return "revisar" if danger_idx >= p["warn"] else "permitir"
    risky = answer != KINDS[kind]["safe"]
    sure = prob >= p["min_p"]
    if danger_idx >= p["block"] and risky and sure:
        return "bloquear"
    if danger_idx >= p["warn"] or risky:
        return "avisar" if sure else "revisar"
    return "permitir"


def sanitize(text: str, spans, mode: str = "marcar") -> str:
    """Remove ("remover") or highlight ("marcar") passages. spans: [(start, end), ...]."""
    out, last = [], 0
    for a, b in sorted(spans):
        if a < last:
            continue
        out.append(text[last:a])
        out.append("[removido]" if mode == "remover" else f"[[ALERTA: {text[a:b]}]]")
        last = b
    out.append(text[last:])
    return "".join(out)


def segments(text: str, max_segments: int = 24):
    """Split a text into sentence/line segments [(start, end)] for passage-level judgement."""
    import re
    out = []
    for m in re.finditer(r"[^\n.!?]+[.!?]*", text):
        a, b = m.start(), m.end()
        while a < b and text[a].isspace():
            a += 1
        while b > a and text[b - 1].isspace():
            b -= 1
        if b - a >= 3:
            out.append((a, b))
    return out[:max_segments]
