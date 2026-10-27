"""Usage advice and the generic safety response.

1. Generic safety response: when the QUESTION itself (ask / options / propositions - what the caller wants decided)
   asks for something harmful (how to build weapons, make drugs, write malware, sexual content with minors,
   discriminatory profiling), Djorge Origo returns a fixed, generic safety message instead of a decision.
   Harmful words in the ANALYSED text (a ticket, a post to moderate) are NOT refused: classifying them is the
   protective use case. Those answers carry a safety notice (and support resources for self-harm) instead.
2. Advice: cheap checks that explain a bad result before the user blames the model - mixed languages, text too short
   or garbled, labels that overlap, too many labels without descriptions, and why an answer was withheld.
All messages exist in pt / en / es. Nothing is logged.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Dict, List, Optional

# categories whose presence IN THE QUESTION means "the caller asks for harm" -> generic response
QUESTION_REFUSE = {"violence_weapons", "illegal_drugs", "cyber_abuse", "sexual_minors", "discriminatory_profiling"}

SAFETY_RESPONSE = {
    "pt": "Não posso ajudar com esse pedido. O Djorge Origo analisa e classifica textos, mas não responde a pedidos "
          "que possam causar danos a pessoas. Se você estiver analisando um conteúdo (moderação, triagem), envie-o no "
          "campo de texto e pergunte, por exemplo, \"este texto é perigoso?\".",
    "en": "I can't help with that request. Djorge Origo analyses and classifies text, but it does not answer requests "
          "that could cause harm. If you are analysing content (moderation, triage), send it as the text and ask, for "
          "example, \"is this text dangerous?\".",
    "es": "No puedo ayudar con esa solicitud. Djorge Origo analiza y clasifica textos, pero no responde a pedidos que "
          "puedan causar daño. Si estás analizando un contenido (moderación, triaje), envíalo como texto y pregunta, por "
          "ejemplo, \"¿este texto es peligroso?\".",
}
NOTICE = {
    "pt": "O texto analisado contém conteúdo sensível. A resposta é apenas uma classificação do texto.",
    "en": "The analysed text contains sensitive content. The answer is only a classification of the text.",
    "es": "El texto analizado contiene contenido sensible. La respuesta es solo una clasificación del texto.",
}

ADVICE = {
    "mixed_languages": {
        "pt": "A pergunta, as opções e o texto estão em línguas diferentes ({langs}). Para resultados melhores, escreva tudo na mesma língua.",
        "en": "The question, options and text are in different languages ({langs}). For better results, write everything in one language.",
        "es": "La pregunta, las opciones y el texto están en idiomas distintos ({langs}). Para mejores resultados, escribe todo en un solo idioma."},
    "text_too_short": {
        "pt": "O texto é muito curto para decidir com segurança. Envie a mensagem completa.",
        "en": "The text is too short to decide reliably. Send the full message.",
        "es": "El texto es demasiado corto para decidir con seguridad. Envía el mensaje completo."},
    "garbled_text": {
        "pt": "O texto parece corrompido ou digitado ao acaso. Confira a codificação (UTF-8) e escreva frases completas.",
        "en": "The text looks garbled or random. Check the encoding (UTF-8) and write complete sentences.",
        "es": "El texto parece dañado o escrito al azar. Revisa la codificación (UTF-8) y escribe frases completas."},
    "overlapping_labels": {
        "pt": "Algumas opções se sobrepõem ({labels}). Use opções mutuamente exclusivas ou adicione uma descrição a cada uma.",
        "en": "Some options overlap ({labels}). Use mutually exclusive options or add a description to each.",
        "es": "Algunas opciones se solapan ({labels}). Usa opciones excluyentes o añade una descripción a cada una."},
    "many_labels": {
        "pt": "Há muitas opções sem descrição. Descreva cada opção em poucas palavras ou divida em duas perguntas.",
        "en": "There are many options without descriptions. Describe each option briefly or split into two questions.",
        "es": "Hay muchas opciones sin descripción. Describe cada opción brevemente o divide en dos preguntas."},
    "safety_disagreement": {
        "pt": "A classificação de segurança do modelo e a checagem por palavras-chave discordam ({cat}). Recomendamos revisão humana.",
        "en": "The model's safety classification and the keyword check disagree ({cat}). Human review is recommended.",
        "es": "La clasificación de seguridad del modelo y la revisión por palabras clave no coinciden ({cat}). Se recomienda revisión humana."},
    "low_confidence": {
        "pt": "O modelo não teve certeza suficiente ({p:.0%}) e preferiu não responder. A sugestão mais provável está em 'proposed_value'. Reformule de forma mais clara, descreva as opções ou reduza a exigência de certeza.",
        "en": "The model was not confident enough ({p:.0%}) and withheld the answer. The most likely option is in 'proposed_value'. Rephrase more clearly, describe the options or lower the certainty requirement.",
        "es": "El modelo no tuvo suficiente certeza ({p:.0%}) y prefirió no responder. La opción más probable está en 'proposed_value'. Reformula con más claridad, describe las opciones o reduce la exigencia de certeza."},
    "no_evidence": {
        "pt": "As opções são citadas no texto, mas o texto não diz qual delas responde à pergunta.",
        "en": "The options are mentioned in the text, but the text does not say which one answers the question.",
        "es": "Las opciones aparecen en el texto, pero el texto no dice cuál responde a la pregunta."},
}

_STOP = {
    "pt": {"o", "a", "os", "as", "de", "do", "da", "dos", "das", "que", "e", "é", "em", "um", "uma", "para", "com", "não",
           "por", "mais", "como", "mas", "foi", "ao", "ele", "ela", "seu", "sua", "meu", "minha", "você", "qual", "quem",
           "isso", "este", "esta", "está", "são", "tem", "já", "também", "pelo", "pela", "nos", "nas", "quero", "eu"},
    "en": {"the", "a", "an", "of", "and", "to", "in", "is", "it", "that", "for", "on", "with", "as", "was", "are", "be",
           "this", "my", "i", "you", "your", "what", "which", "who", "not", "have", "has", "from", "at", "by", "should",
           "do", "does", "can", "will", "me", "we", "they", "there", "how", "why", "when"},
    "es": {"el", "la", "los", "las", "de", "del", "que", "y", "en", "un", "una", "para", "con", "no", "por", "más", "como",
           "pero", "fue", "al", "es", "su", "mi", "tu", "usted", "cuál", "quién", "esto", "este", "esta", "está", "son",
           "tiene", "ya", "también", "quiero", "yo", "muy", "hay", "qué", "cómo", "porque"},
}
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


_MARK = {"pt": {"deve", "devo", "atender", "isso", "você", "não", "qual", "quem", "também", "então", "obrigado", "olá"},
         "en": {"should", "which", "what", "please", "thanks", "hello", "would", "could"},
         "es": {"debe", "cuál", "quién", "gracias", "hola", "usted", "también", "entonces", "qué"}}
_CHARS = {"pt": "ãõç", "es": "ñ¿¡", "en": ""}


def lang_of(text: str) -> Optional[str]:
    """pt / en / es by stop-word + marker votes; None when there is too little signal."""
    t = (text or "").casefold()
    ws = _WORD.findall(t)
    if len(ws) < 2:
        return None
    votes = {l: sum(w in s for w in ws) + sum(w in _MARK[l] for w in ws) + 2 * sum(c in t for c in _CHARS[l])
             for l, s in _STOP.items()}
    best = max(votes, key=votes.get)
    second = sorted(votes.values())[-2]
    if len(ws) < 6:
        return best if votes[best] >= 1 and second == 0 else None
    if votes[best] < 2 or votes[best] < 1.5 * max(second, 1):
        return None
    return best


def _garbled(text: str) -> bool:
    t = text.strip()
    if len(t) < 12:
        return False
    if "�" in t or re.search(r"Ã[\x80-\xbf¡-¿]|â€", t):               # mojibake / replacement chars
        return True
    letters = sum(ch.isalpha() for ch in t)
    if letters / max(len(t), 1) < 0.35:
        return True
    if re.search(r"(.)\1{7,}", t):
        return True
    ws = _WORD.findall(t)
    vowels = set("aeiouáéíóúâêôãõàyAEIOUÁÉÍÓÚ")
    longs = [w for w in ws if len(w) >= 7]
    if longs and sum(not any(c in vowels for c in w) for w in longs) / len(longs) > 0.5:
        return True
    return False


def _fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s.casefold()) if not unicodedata.combining(c)).strip()


def question_parts(prog) -> List[str]:
    """Everything the caller wrote as the QUESTION (not the analysed text)."""
    from ..lang.ast import Decision
    out = []
    for st in prog.statements:
        e = st.expr
        if isinstance(e, Decision):
            out += [e.ask, *e.options, *e.descriptions]
        else:
            r = e.resolution
            out += [str(r.target), r.definition or ""]
    return [x for x in out if x]


def text_parts(prog, source, sources) -> List[str]:
    out = [source or "", *((sources or {}).values())]
    for st in prog.statements:
        src = st.expr.source
        if src.literal is not None:
            out.append(src.literal)
    return [x for x in out if x]


def safety_response(lang: str, report: dict) -> dict:
    lang = lang if lang in SAFETY_RESPONSE else "pt"
    r = {"type": "safety", "decision": None, "answer": None, "abstain": True, "refused": True,
         "message": SAFETY_RESPONSE[lang], "categories": sorted(report.get("actions", {})), "engine": "safety"}
    if report.get("support"):
        r["support"] = report["support"]
    return r


def advise(prog, result: dict, source, sources, lang: str = "pt") -> List[dict]:
    """Advice for one request. Cheap (regex + counting); never raises."""
    try:
        return _advise(prog, result, source, sources, lang if lang in ("pt", "en", "es") else "pt")
    except Exception:
        return []


def _advise(prog, result, source, sources, lang):
    from ..lang.ast import Decision
    out, seen = [], set()

    def add(code, **kw):
        if code not in seen:
            seen.add(code)
            out.append({"code": code, "message": ADVICE[code][lang].format(**kw)})

    q_parts = question_parts(prog)
    t_parts = text_parts(prog, source, sources)
    langs = {}
    for name, txt in (("question", " ".join(q_parts)), ("text", " ".join(t_parts))):
        l = lang_of(txt)
        if l:
            langs[name] = l
    for st in prog.statements:                                         # options in a different language than the ask
        e = st.expr
        if isinstance(e, Decision) and e.ask:
            la, lo = lang_of(e.ask), lang_of(" ".join(e.options + e.descriptions))
            if la:
                langs["question"] = la
            if lo and lo != la:
                langs["options"] = lo
    if len(set(langs.values())) > 1:
        add("mixed_languages", langs=", ".join(f"{k}: {v}" for k, v in langs.items()))
    for txt in t_parts:
        if _garbled(txt):
            add("garbled_text")
    decisions = [st.expr for st in prog.statements if isinstance(st.expr, Decision)]
    for e in decisions:
        txt_words = sum(len(_WORD.findall(t)) for t in t_parts)
        if e.kind in ("CHOICE", "SET", "SCORE") and 0 < txt_words < 3:
            add("text_too_short")
        if e.kind in ("CHOICE", "SET"):
            fl = [_fold(o) for o in e.options]
            over = [(a, b) for i, a in enumerate(fl) for b in fl[i + 1:] if a and b and (a in b or b in a)]
            if over:
                add("overlapping_labels", labels=", ".join(f"{a} / {b}" for a, b in over[:3]))
            if len(e.options) > 12 and not any(e.descriptions):
                add("many_labels")
    rs = [result] if "type" in (result or {}) else list((result or {}).values())
    for r in rs:
        if isinstance(r, dict) and r.get("abstain") and not r.get("refused"):
            if r.get("grounding") in ("classifier_only", "evidence_ambiguous"):
                add("no_evidence")
            else:
                p = r.get("probability")
                add("low_confidence", p=float(p) if isinstance(p, (int, float)) else 0.0)
    return out


# ------------------------------------------------------------------------------------- sensitive-domain flags --
# Djorge Origo never gives advice, but callers often USE its decisions in sensitive domains. When the question or the
# text belongs to one of them, the response carries a flag and a disclaimer so the caller (and the end user) knows the
# decision is a text classification, not professional advice. Cue lists are conservative (pt / en / es).
DOMAIN_CUES = {
    "financial": r"\b(invest\w*|a[cç](ao|oes|ões|ciones)|stocks?|bolsa de valores|cripto\w*|bitcoin|criptomoned\w*|"
                 r"empr[eé]stimo\w*|loan|pr[eé]stamo|financiamento|cr[eé]dito|credit score|score de cr[eé]dito|juros|"
                 r"interest rate|tasa de inter[eé]s|aposentadoria|retirement|jubilaci[oó]n|renda fixa|tesouro direto|"
                 r"comprar ou vender|buy or sell|portfolio|carteira de investimentos|dividend\w*|forex|trading)\b",
    "health": r"\b(sintoma\w*|symptom\w*|s[ií]ntoma\w*|diagn[oó]stic\w*|diagnos\w*|rem[eé]dio\w*|medicament\w*|"
              r"medication\w*|medicine|dose|dosage|dosis|tratamento|treatment|tratamiento|doen[cç]a\w*|disease|"
              r"enfermedad\w*|gravidez|pregnan\w*|embarazo|c[aâ]ncer|diabetes|depress\w*|ansiedade|anxiety|ansiedad|"
              r"m[eé]dico|doctor|hospital|vacina\w*|vaccin\w*|vacuna\w*)\b",
    "legal": r"\b(processo judicial|a[cç][aã]o judicial|lawsuit|demanda judicial|advogad\w*|lawyer|abogad\w*|"
             r"cl[aá]usula\w*|clause\w*|contrato de \w+|breach of contract|incumplimiento|meus direitos|direitos do consumidor|"
             r"direito trabalhista|legal rights?|mis derechos|crime\w*|delito\w*|tribunal|court|juiz|judge|juez|"
             r"indeniza[cç]\w*|compensation claim|rescis[aã]o|trabalhist\w*|labor law|lgpd|gdpr|procon|"
             r"boletim de ocorr[eê]ncia|police report|denuncia penal)\b",
    "political": r"\b(elei[cç](ao|oes|ões)|election\w*|elecci[oó]n\w*|em quem votar|who to vote for|votar en|"
                 r"candidat[oa]s? a|partido pol[ií]tico|political party|partido (pt|pl|psdb|mdb|psol|novo|uni[aã]o)|"
                 r"deputad[oa]s?|senador\w*|senator|congressman|congressista|vereador\w*|"
                 r"esquerda pol[ií]tica|direita pol[ií]tica|left-wing|right-wing|extrema[- ](direita|esquerda)|"
                 r"governo (federal|lula|bolsonaro)|propaganda eleitoral|campaign ad)\b",
    "ethical": r"\b(contrat(ar|a[cç][aã]o) ou n[aã]o|hire or not|contratar o no|demitir|fire (him|her|them)|despedir|"
               r"aprovar (o )?cr[eé]dito (de|para)|deny (the )?(loan|credit)|negar (o )?(cr[eé]dito|empr[eé]stimo)|"
               r"vigil[aâ]ncia|surveillance|monitorar funcion[aá]rios|monitor employees|reconhecimento facial|"
               r"facial recognition|perfil de risco de (pessoas|clientes)|risk profile of (people|customers)|"
               r"pena de morte|death penalty|eutan[aá]sia|euthanasia|aborto|abortion)\b",
}
_DOMAIN_RX = {k: re.compile(v, re.I) for k, v in DOMAIN_CUES.items()}
DISCLAIMER = {
    "financial": {"pt": "Classificação automática de texto, não é recomendação financeira ou de investimento.",
                  "en": "Automated text classification, not financial or investment advice.",
                  "es": "Clasificación automática de texto, no es asesoramiento financiero ni de inversión."},
    "health": {"pt": "Classificação automática de texto, não é diagnóstico nem orientação médica. Procure um profissional de saúde.",
               "en": "Automated text classification, not a diagnosis or medical advice. Consult a health professional.",
               "es": "Clasificación automática de texto, no es diagnóstico ni consejo médico. Consulta a un profesional de salud."},
    "legal": {"pt": "Classificação automática de texto, não é parecer ou aconselhamento jurídico. Consulte um advogado.",
              "en": "Automated text classification, not legal advice. Consult a lawyer.",
              "es": "Clasificación automática de texto, no es asesoramiento jurídico. Consulta a un abogado."},
    "political": {"pt": "Classificação automática de texto, sem posição política. Não use como orientação de voto.",
                  "en": "Automated text classification with no political position. Do not use it as voting guidance.",
                  "es": "Clasificación automática de texto, sin posición política. No la uses como orientación de voto."},
    "ethical": {"pt": "Decisão com impacto sobre pessoas: use apenas como apoio e mantenha revisão humana.",
                "en": "Decision affecting people: use only as support and keep human review.",
                "es": "Decisión que afecta a personas: úsala solo como apoyo y mantén revisión humana."},
}


def domain_flags(texts: List[str], lang: str = "pt") -> dict:
    lang = lang if lang in ("pt", "en", "es") else "pt"
    joined = "\n".join(t for t in texts if t)[:20000]
    found = [k for k, rx in _DOMAIN_RX.items() if rx.search(joined)]
    return {"flags": found, "disclaimers": [DISCLAIMER[k][lang] for k in found]} if found else {}


# ------------------------------------------------------------------------- second opinion on safety labels --
SAFETY_LABEL = {   # label words -> guard category
    "self_harm": r"automutila|auto-?les|self[- ]?harm|suic|se machucar|hurt (them|him|her)sel|risco de vida",
    "hate_harassment": r"[oó]dio|hate|racis|discrimina|ass[eé]dio|harass|xenof|homof",
    "violence_weapons": r"viol[eê]n|amea[cç]a|threat|weapon|arma|terror",
    "manipulation": r"inje[cç]|injection|jailbreak|manipula|golpe|scam|phishing",
}
SAFE_LABEL = r"^(safe|seguro|sem risco|no risk|sin riesgo|ok|normal|benign\w*|neutr\w*|nenhum|none|ninguno)$"
_SL = {k: re.compile(v, re.I) for k, v in SAFETY_LABEL.items()}
_SAFE = re.compile(SAFE_LABEL, re.I)


def second_opinion(result: dict, texts, guard, lang="pt"):
    """Cross-check a SAFETY decision with the lexical gate. Disagreement does not change the answer; it marks the
    response for human review: 'risk' with no cue at all in the text (e.g. 'cortei o dedo descascando batata'), or
    'safe' while the text has a STRONG cue (e.g. 'vou me matar')."""
    try:
        if not isinstance(result, dict) or result.get("decision") != "CHOICE" or result.get("abstain"):
            return None
        ans = str(result.get("answer", ""))
        cues = guard.lexical("\n".join(t for t in texts if t))
        cats = [k for k, rx in _SL.items() if rx.search(ans)]
        if cats:
            missing = [c for c in cats if c not in cues]
            if missing:
                return {"needs_review": True, "reason": "model_flags_risk_without_cue", "category": missing[0]}
        elif _SAFE.match(ans.strip()):
            strong = [c for c, v in cues.items() if v >= 1.0 and c in _SL]
            if strong:
                return {"needs_review": True, "reason": "model_says_safe_with_strong_cue", "category": strong[0]}
        return None
    except Exception:
        return None
