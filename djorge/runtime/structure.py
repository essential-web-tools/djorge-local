"""Structured state: turn semantic spans into explicit, checkable facts.

    mention   = span + class + polarity (asserted | negated | nonfactual) + quantity
    fact      = (entity, value, unit, sentence_index)          for comparisons
    evidence  = grounded answer spans from the ANSWER resolution, used to verify CHOICE options

Everything here is deterministic and documented; the semantic parts (which spans,
which polarity, which answer span) come from the trained model.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Dict, List, Optional, Sequence, Tuple

from . import text as T

NUMWORDS = {
    # en
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "dozen": 12, "single": 1,
    # pt
    "um": 1, "uma": 1, "dois": 2, "duas": 2, "três": 3, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6, "sete": 7, "oito": 8,
    "nove": 9, "dez": 10, "dúzia": 12,
    # es
    "un": 1, "una": 1, "uno": 1, "dos": 2, "cuatro": 4, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
    # fr / de
    "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "ein": 1, "eine": 1, "einen": 1, "zwei": 2, "drei": 3, "vier": 4, "fünf": 5,
}
ZERO = {"no", "nenhum", "nenhuma", "ningún", "ninguna", "aucun", "aucune", "kein", "keine", "zero", "none"}


def fold(s: str) -> str:
    return unicodedata.normalize("NFKC", s).casefold()


def quantity_before(source: str, start: int) -> Optional[float]:
    """Number written immediately before a mention ('duas maçãs', '2 apples', 'a banana').
    Looks back at most 2 words; returns None if no quantifier (caller treats as 1)."""
    left = source[max(0, start - 40):start]
    toks = re.findall(r"[^\W_]+|\d+(?:[.,]\d+)?", left, re.UNICODE)[-2:]
    for t in reversed(toks):
        f = fold(t)
        if re.fullmatch(r"\d+(?:[.,]\d+)?", f):
            return float(f.replace(",", "."))
        if f in NUMWORDS:
            return float(NUMWORDS[f])
        if f in ZERO:
            return 0.0
    return None


def to_number(s: str) -> Optional[float]:
    s = fold(s).strip()
    m = re.search(r"-?\d+(?:[.,]\d+)?", s)
    if m:
        return float(m.group().replace(",", "."))
    for w in re.findall(r"[^\W_]+", s):
        if w in NUMWORDS and w not in ("a", "an", "un", "um", "uma", "una", "une", "ein", "eine"):
            return float(NUMWORDS[w])
    nums = T.numbers(s)
    return float(nums[0].value) if nums else None


STOP = {"a", "an", "the", "um", "uma", "o", "os", "as", "el", "la", "los", "las", "un", "una", "de", "do", "da", "of",
        "and", "or", "e", "ou", "y", "et", "und", "oder", "with", "com", "con", "avec", "mit", "to", "in", "at", "on",
        "em", "no", "na", "ao", "à", "às", "en", "por", "para", "is", "are", "was", "did", "does", "do", "é", "foi", "está"}
WH = {"who", "whom", "whose", "what", "which", "where", "when", "how", "quem", "qual", "quais", "onde", "quando", "como",
      "que", "quién", "cuál", "dónde", "cuándo", "qué", "qui", "quoi", "où", "quand", "wer", "was", "wo", "wann"}
SUBJECT_WH = {"who", "quem", "quién", "quien", "qui", "wer"}
NEG_CUES = {"not", "n't", "never", "no", "none", "neither", "nor", "without", "não", "nunca", "nenhum", "nenhuma", "nem",
            "sem", "jamais", "ningún", "ninguna", "sin", "ne", "pas", "aucun", "aucune", "kein", "keine", "nicht", "nie"}
NOW_CUES = {"now", "currently", "current", "agora", "atualmente", "atual", "ahora", "actualmente", "actual", "maintenant"}
CHANGE_CUES = {"then", "later", "afterwards", "moved", "changed", "became", "painted", "rescheduled", "now", "but",
               "depois", "mudou", "mudaram", "passou", "virou", "pintado", "pintada", "agora", "mas", "mais tarde",
               "luego", "después", "cambió", "pintaron", "pero", "ahora"}
_BOUND = re.compile(r"[;!?]|(?<!\d)\.|\.(?!\d)|,?\s+(?:but|mas|pero|mais|aber|however|porém)\s+|,\s+(?:and|e|y)\s+|\s+(?:and|e|y|while|enquanto|mientras)\s+", re.I)


def clauses(source: str):
    """(start, end) char ranges of clauses: sentence punctuation, ';', contrastive and coordinating conjunctions."""
    out, s = [], 0
    for m in _BOUND.finditer(source):
        if m.start() > s:
            out.append((s, m.start()))
        s = m.end()
    if s < len(source):
        out.append((s, len(source)))
    return out


def clause_of(source: str, start: int):
    for a, b in clauses(source):
        if a <= start < b:
            return a, b
    return 0, len(source)


def _words(s):
    return [fold(w) for w in re.findall(r"[^\W_]+(?:'t)?", s, re.UNICODE)]


def has_negation(text: str) -> bool:
    ws = _words(text)
    return any(w in NEG_CUES or w.endswith("n't") for w in ws)


def negation_in_scope(source: str, start: int) -> bool:
    """NegEx-style scope: a negation cue can only negate a mention inside the same clause."""
    a, b = clause_of(source, start)
    return has_negation(source[a:b])


_ROLE_PATTERNS = [
    # "The courier (today) was Omar" / "O entregador (hoje) era o Omar" / "El mensajero era Omar"
    r"\b(?i:the|o|a|el|la|le|der|die)\s+([^\W\d_]{3,})(?:\s+(?i:today|hoje|hoy|this\s+year|deste\s+ano|de\s+este\s+año|that\s+day|naquele\s+dia|ese\s+día))?\s+(?i:was|is|era|é|foi|fue|es|était|est|war|ist)\s+(?:o\s+|a\s+)?([A-ZÁÉÍÓÚÂÊÔÃÕÇ][\w\-]+)",
    # "Omar was the courier" / "Omar era o entregador" / "Omar, the courier,"
    r"\b([A-ZÁÉÍÓÚÂÊÔÃÕÇ][\w\-]+)\s*(?:,|\s(?i:was|is|era|é|foi|fue|es|était|est|war|ist))\s+(?i:the|o|a|el|la|le|der|die)\s+([^\W\d_]{3,})",
]


def role_bindings(source: str) -> Dict[str, str]:
    """Explicit role->name bindings stated in the text, e.g. {"courier": "Omar"}.
    Only explicit copula statements are used (no guessing from pronouns)."""
    out: Dict[str, str] = {}
    for i, pat in enumerate(_ROLE_PATTERNS):
        for m in re.finditer(pat, source):
            role, name = (m.group(1), m.group(2)) if i == 0 else (m.group(2), m.group(1))
            if role[0].isupper() or name.lower() in STOP:
                continue
            out.setdefault(fold(role), name)
    return out


def resolve_roles(source: str, bindings: Dict[str, str] = None) -> str:
    """Rewrite later role mentions ("the courier") as the bound name ("Omar") so distant subjects
    become local. Used by the rule layer and the evidence verifier; offsets of the original text are not used."""
    b = bindings if bindings is not None else role_bindings(source)
    if not b:
        return source
    out = source
    for role, name in b.items():
        out = re.sub(r"\b(?:the|o|a|el|la|le|der|die)\s+" + re.escape(role) + r"\b(?!\s+(?:\w+\s+)?(?:was|is|era|é|foi|fue|es)\s)",
                     name, out, flags=re.I)
    return out


def anchor_choice(source: str, question: str, options):
    """Deterministic rule: the unique option sharing a non-negated clause with every question anchor word
    that occurs in the source. Skipped for 'now/current' questions (state questions) and negated questions."""
    source = resolve_roles(source)                     # distant subject -> local name
    qw = [w for w in _words(question) if w not in STOP and w not in WH and len(w) > 1]
    opt_words = {T.norm_token(w) for o in options for w in _words(o)}
    if has_negation(question) or any(w in NOW_CUES for w in qw):
        return None
    src_norm = {T.norm_token(w) for w in _words(source)}
    anchors = [w for w in qw if T.norm_token(w) in src_norm and T.norm_token(w) not in opt_words]
    if not anchors:
        return None
    hits = []
    opt_tok = {T.norm_token(w) for o in options for w in _words(o)}
    merged = []                       # a bare noun phrase after 'and' ("..., and a chair") is not its own clause
    for a, b in clauses(source):
        rest = [w for w in _words(source[a:b]) if T.norm_token(w) not in opt_tok and w not in STOP]
        if merged and not rest:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    for a, b in merged:
        cl = source[a:b]
        cw = {T.norm_token(w) for w in _words(cl)}
        if all(T.norm_token(x) in cw for x in anchors) and not has_negation(cl):
            hits += [(o, cl) for o in options if T.find_id(cl, o)]
    found = {o for o, _ in hits}
    WHO = {"who", "quem", "quién", "qui", "wer"}
    qwords = _words(question)
    if len(found) > 1 and qwords and qwords[0] in WHO:
        # "Who <verb> ...?" with the question's own verb present in the clause: the answer is the subject,
        # i.e. the option written BEFORE that verb ("Omar handed the parcel to Lena" -> Omar).
        verb = next((w for w in qwords[1:] if w not in STOP and w not in WH), None)
        if verb and T.norm_token(verb) in src_norm:
            subj = set()
            for o, cl in hits:
                cw = [T.norm_token(w) for w in _words(cl)]
                if T.norm_token(verb) in cw:
                    vpos = cl.casefold().find(verb.casefold())
                    m = T.find_id(cl, o)
                    if m and vpos >= 0 and m[0].start < vpos:
                        subj.add(o)
            if len(subj) == 1:
                found = subj
    if len(found) == 1:
        o = found.pop()
        cl = next(c for x, c in hits if x == o)
        # role guard: for subject questions ("who rides...?") the answer must come BEFORE the anchor words
        # ("Lena rides a scooter"); for object questions ("what does X teach?") it must come AFTER them.
        # "The courier handed the parcel to B" -> "Who handed over the parcel?" -> B is after -> no rule.
        qwords = _words(question)
        subject_q = bool(qwords) and qwords[0] in SUBJECT_WH
        span = T.find_id(cl, o)[0]
        cw = [(m.start(), T.norm_token(m.group().casefold())) for m in re.finditer(r"[^\W_]+", cl)]
        apos = [p for p, w in cw if w in {T.norm_token(a) for a in anchors}]
        if apos:
            before = span.start < min(apos)
            after = span.start > max(apos)
            if (subject_q and not before) or (not subject_q and not after):
                return None
        return {"option": o, "anchors": anchors, "clause": cl.strip()}
    return None


# ------------------------------------------------------------ transfers --
# Converse roles of transfer events: "A sold X to B" == "B bought X from A".
# GIVE verbs put the giver in subject position and the receiver after to/para/a;
# GET verbs put the receiver in subject position and the giver after from/de.
GIVE_VERBS = {"gave", "give", "gives", "sold", "sell", "sells", "sent", "send", "sends", "lent", "lend", "lends",
              "handed", "hands", "passed", "mailed", "wrote", "delivered", "donated",
              "deu", "dá", "vendeu", "vende", "enviou", "envia", "mandou", "manda", "emprestou", "empresta",
              "entregou", "entrega", "escreveu", "doou", "presenteou",
              "dio", "vendió", "envió", "mandó", "prestó", "entregó", "escribió", "regaló", "donó"}
GET_VERBS = {"received", "receives", "got", "gets", "bought", "buys", "borrowed", "borrows", "obtained", "inherited",
             "recebeu", "recebe", "ganhou", "comprou", "compra", "herdou",
             "recibió", "recibe", "compró", "obtuvo", "heredó"}
RECV_Q = {"received", "receive", "receives", "got", "get", "gets", "bought", "buy", "buys", "borrowed", "borrow",
          "obtained", "inherited", "recebeu", "recebe", "ganhou", "comprou", "compra", "herdou", "pegou",
          "recibió", "recibe", "compró", "obtuvo", "heredó"}
GIVE_Q = {"gave", "give", "gives", "sold", "sell", "sells", "sent", "send", "lent", "lend", "handed", "wrote", "write",
          "delivered", "donated", "deu", "vendeu", "vende", "enviou", "mandou", "emprestou", "entregou", "escreveu",
          "doou", "presenteou", "dio", "vendió", "envió", "mandó", "prestó", "entregó", "escribió", "regaló", "donó"}
PASSIVE = {"was", "were", "been", "being", "foi", "foram", "fue", "fueron", "sido"}


def transfer_choice(source: str, question: str, options):
    """Deterministic converse-role rule for transfer events (give/sell/send/lend <-> receive/buy/borrow).
    Fires only for 'who'-questions with a transfer verb, a non-negated active clause naming exactly two options
    (one before the verb, one after it), and returns the option in the role the question asks for."""
    qw = _words(question)
    if not qw or has_negation(question):
        return None
    to_whom = len(qw) > 1 and qw[1] in ("whom", "quem", "quién") and qw[0] in ("to", "para", "a", "from", "de")
    if qw[0] not in SUBJECT_WH and not to_whom:
        return None
    if to_whom:
        want = "receiver" if qw[0] in ("to", "para", "a") else "giver"
    else:
        qv = [w for w in qw[1:4] if w in RECV_Q or w in GIVE_Q]
        if not qv:
            return None
        want = "receiver" if qv[0] in RECV_Q else "giver"
    q_nouns = [w for w in qw if w not in STOP and w not in WH and w not in RECV_Q and w not in GIVE_Q and len(w) > 2]
    source = resolve_roles(source)
    found = []
    for a, b in clauses(source):
        cl = source[a:b]
        toks = [(m.start(), fold(m.group())) for m in re.finditer(r"[^\W_]+", cl)]
        verbs = [(p, w) for i, (p, w) in enumerate(toks)
                 if (w in GIVE_VERBS or w in GET_VERBS) and not (i and toks[i - 1][1] in PASSIVE)]
        if len(verbs) != 1 or has_negation(cl):
            continue
        vpos, verb = verbs[0]
        cw = {T.norm_token(w) for _, w in toks}
        nouns_here = [n for n in q_nouns if T.norm_token(n) in {T.norm_token(w) for w in _words(source)}]
        if nouns_here and not all(T.norm_token(n) in cw for n in nouns_here):
            continue
        before, after = [], []
        for o in options:
            m = T.find_id(cl, o)
            if not m:
                continue
            (before if m[0].start < vpos else after).append((m[0].start, o))
        if len(before) != 1 or len(after) != 1:
            continue
        subj, other = before[0][1], after[0][1]
        giver, receiver = (subj, other) if verb in GIVE_VERBS else (other, subj)
        found.append({"option": receiver if want == "receiver" else giver, "verb": verb, "giver": giver,
                      "receiver": receiver, "asked": want, "clause": cl.strip()})
    answers = {f["option"] for f in found}
    return found[0] if len(answers) == 1 else None


NAME_CLASSES = {"person", "people", "name", "company", "organization", "organisation", "brand", "location", "place", "city",
                "country", "state", "team", "product", "pessoa", "nome", "empresa", "organização", "marca", "local", "lugar",
                "cidade", "país", "estado", "time", "produto", "persona", "compañía", "organización", "ciudad", "equipo"}


def proper_noun_guard(source: str, s: int, e: int, target: str) -> bool:
    """True when a CLASS match for a common-noun class ("Fruit", "Animal") is written as a proper noun:
    capitalised, not sentence-initial, in a language that does not capitalise all nouns (German is skipped).
    'A Apple anunciou...' / 'shares of Orange fell' -> the span is a name, not a member. Halves the probability,
    so the per-item abstention policy turns a confident miscount into an honest abstention."""
    span = source[s:e]
    if not span[:1].isupper() or fold(target or "") in NAME_CLASSES or span.isupper() and len(span) <= 3:
        return False
    prev = source[:s].rstrip()
    if not prev or prev[-1] in ".!?:\n\"'«“(":
        return False
    ws = set(_words(source))
    if len(ws & {"der", "die", "das", "und", "ist", "nicht", "ein", "eine", "einen", "mit", "ich", "den", "dem", "auf", "zu"}) >= 2:
        return False
    return True


def recency_choice(source: str, question: str, options):
    """Deterministic rule for current-state questions ('now', 'agora'): if >= 2 options are mentioned and the
    source contains a change cue, the option mentioned LAST is the current state."""
    if not any(w in NOW_CUES for w in _words(question)):
        return None
    sw = " " + " ".join(_words(source)) + " "
    if not any(f" {c} " in sw for c in CHANGE_CUES):
        return None
    pos = {}
    for o in options:
        m = T.find_id(source, o)
        if m:
            pos[o] = m[-1].start
    if len(pos) < 2:
        return None
    o = max(pos, key=pos.get)
    return {"option": o, "mentioned": sorted(pos, key=pos.get)}


# ------------------------------------------------------------ comparisons --
COMPARATIVES = {   # cue -> (direction, unit hint)
    "taller": ("max", "height"), "shorter": ("min", "height"), "older": ("max", "age"), "younger": ("min", "age"),
    "heavier": ("max", None), "lighter": ("min", None), "more expensive": ("max", None), "cheaper": ("min", None),
    "farther": ("max", None), "closer": ("min", None), "bigger": ("max", None), "smaller": ("min", None),
    "mais alto": ("max", "height"), "mais alta": ("max", "height"), "mais baixo": ("min", "height"), "mais baixa": ("min", "height"),
    "mais velho": ("max", "age"), "mais velha": ("max", "age"), "mais novo": ("min", "age"), "mais nova": ("min", "age"),
    "mais caro": ("max", None), "mais barato": ("min", None), "maior": ("max", None), "menor": ("min", None),
    "más alto": ("max", "height"), "más alta": ("max", "height"), "más bajo": ("min", "height"), "mayor": ("max", "age"),
    "menor de edad": ("min", "age"), "más viejo": ("max", "age"), "más joven": ("min", "age"),
    # temporal order over stated years/times
    "last": ("max", "time"), "most recently": ("max", "time"), "latest": ("max", "time"), "first": ("min", "time"),
    "earliest": ("min", "time"), "por último": ("max", "time"), "último": ("max", "time"), "mais recente": ("max", "time"),
    "primeiro": ("min", "time"), "por primera vez": ("min", "time"), "más reciente": ("max", "time"),
}


def comparative(question: str) -> Optional[Tuple[str, Optional[str]]]:
    q = fold(question)
    for cue in sorted(COMPARATIVES, key=len, reverse=True):
        if re.search(r"(?<!\w)" + re.escape(cue) + r"(?!\w)", q):
            return COMPARATIVES[cue]
    return None


def entity_facts(source: str, entities: Sequence[str]) -> Dict[str, float]:
    """entity -> the number stated in the same sentence (first number after the entity).
    Only sentences mentioning exactly one candidate entity and at least one number are used."""
    facts: Dict[str, float] = {}
    for sent in T.sentences(source) or [T.Span(source, 0, len(source))]:
        st = sent.text
        # split coordinated clauses: "A is 25 and B is 31"
        for clause in re.split(r"(?<!\d),\s*|,\s+|\s+(?:and|e|y|et|und|while|enquanto|mientras)\s+", st):
            present = [e for e in entities if T.find_id(clause, e)]
            nums = [n for n in T.numbers(clause, words_too=True) if n.value is not None]
            if len(present) == 1 and nums and present[0] not in facts:
                facts[present[0]] = float(nums[0].value)
    return facts


# ----------------------------------------------------------- verification --
def _content(s: str) -> List[str]:
    stop = STOP
    return [T.norm_token(w) for w in re.findall(r"[^\W_]+", s, re.UNICODE) if fold(w) not in stop]


def _tok_match(a: str, b: str) -> bool:
    return a == b or (min(len(a), len(b)) >= 4 and (a.startswith(b[:4]) and b.startswith(a[:4])))   # opened~open


def support(option: str, evidence: Sequence[Tuple[str, float]]) -> Tuple[float, str]:
    """Probability-weighted support for an option from evidence spans [(text, p)].
    Numeric options match numerically; textual options by content-token coverage F1
    against the union of evidence (so 'a car and a banana' beats 'a car' when both are evidence)."""
    if not evidence:
        return 0.0, ""
    on = to_number(option)
    if on is not None and _content(option) and all(re.fullmatch(r"\d+(\.\d+)?", t) or t in NUMWORDS for t in _content(option)):
        best = max(((p, t) for t, p in evidence if to_number(t) == on), default=(0.0, ""))
        return best
    ot = _content(option)
    if not ot:
        return 0.0, ""
    strong = [(t, p) for t, p in evidence if p >= 0.3]
    ev_toks = [(w, p, t) for t, p in strong for w in _content(t)]
    if not ev_toks:
        return 0.0, ""
    matched = [max((p for w, p, _ in ev_toks if _tok_match(o, w)), default=0.0) for o in ot]
    if not any(matched):
        return 0.0, ""
    recall = sum(matched) / len(ot)
    ev_words = {w for w, _, _ in ev_toks}
    precision = sum(1 for w in ev_words if any(_tok_match(o, w) for o in ot)) / max(len(ev_words), 1)
    f1 = 2 * recall * precision / max(recall + precision, 1e-9)
    texts = ", ".join(sorted({t for w, p, t in ev_toks if any(_tok_match(o, w) for o in ot)}))
    return f1, texts
