"""DJORGE DSL lexer + recursive-descent parser.

Grammar (keywords case-insensitive; targets and strings case-preserved)
-----------------------------------------------------------------------
program    := stmt { ";" stmt } [";"]
stmt       := [ NAME "=" ] expr
expr       := [ op ] resolution from { modifier }
            | decision from { modifier }
op         := GET | COUNT | REPORT | EXISTS | TOTAL
resolution := MODE target
MODE       := ID | CLASS | TYPE | REGEX | ENTITY | AUTO | ANSWER | AGG | AGID
              AGG / AGID = CLASS / ID selectors that also carry each mention's quantity (TOTAL AGG fruta)
target     := WORD | STRING | /regex/flags
decision   := CHOICE options | SCORE options | RANK options | SET options | NOUL STRING
options    := "[" STRING {"," STRING} "]" | "{" STRING ":" STRING {"," ...} "}"
from       := FROM ( STRING | NAME )
modifier   := AT INT | THRESHOLD NUMBER | ASK STRING | DEFINE STRING | EXACT | MENTIONS

Examples
  COUNT ID Arthur FROM "Arthur foi ao mercado."
  REPORT CLASS Fruit FROM source
  CLASS Fruit FROM "Arthur comprou maçãs, uma banana e duas laranjas."
  GET TYPE number AT 2 FROM "12 dogs, 4 cats"
  CHOICE {"billing": "payments, refunds", "technical": "bugs"} FROM ticket ASK "Which team?"
  a = COUNT CLASS Fruit FROM s; b = REPORT CLASS Fruit FROM s      # shared resolution
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from ..errors import DjorgeError
from .ast import Decision, Program, Query, Resolution, Source, Statement

OPS = {"GET", "COUNT", "REPORT", "EXISTS", "TOTAL"}
MODES = {"ID", "CLASS", "TYPE", "REGEX", "ENTITY", "AUTO", "ANSWER", "AGG", "AGID"}
LEGACY_AGG = {"CLASS": "AGG", "ID": "AGID", "AUTO": "AGG", "ENTITY": "AGG"}   # old "AGG CLASS x" = TOTAL AGG x
DECISIONS = {"CHOICE", "SCORE", "NOUL", "RANK", "SET"}
MODIFIERS = {"AT", "THRESHOLD", "ASK", "DEFINE", "EXACT", "MENTIONS"}
KEYWORDS = OPS | MODES | DECISIONS | MODIFIERS | {"FROM"}


class Tok:
    __slots__ = ("kind", "value", "pos")

    def __init__(self, kind, value, pos):
        self.kind, self.value, self.pos = kind, value, pos

    def __repr__(self):
        return f"{self.kind}:{self.value!r}"


def _err(msg, src, pos):
    line = src.count("\n", 0, pos) + 1
    col = pos - (src.rfind("\n", 0, pos) + 1) + 1
    hint = None
    if "unterminated" not in msg and src.count('"') - 2 * src.count('\\"') > 2:
        hint = ("the text may contain quotes: send it in 'sources' and write FROM <name>, "
                "or build the query with djorge.lang.quote.quote()")
    det = {"pos": pos, "line": line, "col": col}
    if hint:
        det["hint"] = hint
    raise DjorgeError("PARSE_ERROR", f"{msg} (line {line}, col {col})" + (f" - hint: {hint}" if hint else ""), det)


def tokenize(src: str) -> List[Tok]:
    i, n, out = 0, len(src), []
    while i < n:
        c = src[i]
        if c.isspace():
            i += 1
        elif c == "#":
            while i < n and src[i] != "\n":
                i += 1
        elif c in "\"'\u201c":
            close = "\u201d" if c == "\u201c" else c          # “typographic quotes” also delimit strings
            j, buf = i + 1, []
            while j < n and src[j] != close:
                if src[j] == "\\" and j + 1 < n:
                    buf.append({"n": "\n", "t": "\t"}.get(src[j + 1], src[j + 1]))
                    j += 2
                    continue
                buf.append(src[j])
                j += 1
            if j >= n:
                _err("unterminated string - a quote inside the text closed it early. Send the text in 'sources' "
                     "and write FROM <name> (recommended), or escape inner quotes as \\\"", src, i)
            out.append(Tok("STRING", "".join(buf), i))
            i = j + 1
        elif c == "/":
            j, buf = i + 1, []
            while j < n and src[j] != "/":
                if src[j] == "\\" and j + 1 < n:
                    buf.append(src[j:j + 2] if src[j + 1] != "/" else "/")
                    j += 2
                    continue
                buf.append(src[j])
                j += 1
            if j >= n:
                _err("unterminated regex", src, i)
            k = j + 1
            while k < n and src[k] in "imsx":
                k += 1
            out.append(Tok("REGEX", ("".join(buf), src[j + 1:k]), i))
            i = k
        elif c.isdigit() or (c in "-." and i + 1 < n and src[i + 1].isdigit()):
            j = i + 1
            while j < n and (src[j].isdigit() or src[j] == "."):
                j += 1
            t = src[i:j]
            try:
                out.append(Tok("NUMBER", float(t) if "." in t else int(t), i))
            except ValueError:
                _err(f"bad number {t!r}", src, i)
            i = j
        elif c.isalpha() or c == "_" or ord(c) > 127:
            j = i + 1
            while j < n and (src[j].isalnum() or src[j] in "_-" or ord(src[j]) > 127):
                j += 1
            w = src[i:j]
            out.append(Tok("KW" if w.upper() in KEYWORDS else "WORD", w, i))
            i = j
        elif c in "[]{},:;=":
            out.append(Tok(c, c, i))
            i += 1
        else:
            _err(f"unexpected character {c!r}", src, i)
    out.append(Tok("EOF", None, n))
    return out


class Parser:
    def __init__(self, src: str):
        self.src, self.t, self.i = src, tokenize(src), 0

    @property
    def tok(self):
        return self.t[self.i]

    def kw(self, *names) -> bool:
        return self.tok.kind == "KW" and self.tok.value.upper() in names

    def take(self):
        t = self.t[self.i]
        self.i += 1
        return t

    def expect(self, kind, what=None):
        if self.tok.kind != kind:
            _err(f"expected {what or kind}, got {self.tok.value!r}", self.src, self.tok.pos)
        return self.take()

    def program(self) -> Program:
        stmts: List[Statement] = []
        names = set()
        while self.tok.kind != "EOF":
            name = None
            if self.tok.kind == "WORD" and self.t[self.i + 1].kind == "=":
                name = self.take().value
                self.take()
            expr = self.expr()
            name = name or f"q{len(stmts) + 1}"
            if name in names:
                _err(f"duplicate statement name {name!r}", self.src, self.tok.pos)
            names.add(name)
            stmts.append(Statement(name, expr))
            if self.tok.kind == ";":
                self.take()
            elif self.tok.kind != "EOF":
                _err(f"expected ';' or end of query, got {self.tok.value!r}", self.src, self.tok.pos)
        if not stmts:
            _err("empty query", self.src, 0)
        if len(stmts) == 1 and stmts[0].name == "q1":
            stmts[0] = Statement("result", stmts[0].expr)
        return Program(tuple(stmts))

    def expr(self):
        if self.kw(*DECISIONS):
            return self.decision()
        op = "MATCH"
        if self.kw("AGG") and self.t[self.i + 1].kind == "KW" and self.t[self.i + 1].value.upper() in LEGACY_AGG:
            self.take()                                  # legacy: AGG CLASS x -> TOTAL AGG x ; AGG ID x -> TOTAL AGID x
            legacy = LEGACY_AGG[self.take().value.upper()]
            self.t.insert(self.i, Tok("KW", legacy, self.tok.pos))
            op = "TOTAL"
        elif self.kw(*OPS):
            op = self.take().value.upper()
        if not self.kw(*MODES):
            got = self.tok.value
            hint = " (did you forget the resolution mode, e.g. ID or CLASS?)" if op != "MATCH" else ""
            _err(f"expected a resolution mode {sorted(MODES)}, got {got!r}{hint}", self.src, self.tok.pos)
        mode = self.take().value.upper()
        tt = self.tok
        flags = ""
        if tt.kind in ("WORD", "STRING"):
            target = self.take().value
        elif tt.kind == "KW":                       # allow keywords as bare targets: ID Set
            target = self.take().value
        elif tt.kind == "REGEX":
            target, flags = self.take().value
        elif tt.kind == "NUMBER":
            target = str(self.take().value)
        else:
            _err(f"expected a target after {mode}", self.src, tt.pos)
        if tt.kind == "REGEX" and mode not in ("REGEX", "AUTO"):
            mode = "REGEX" if mode == "AUTO" else mode
        mods = self.modifiers()
        source = self.from_()
        mods = self.modifiers(mods)
        return Query(op, Resolution(mode, target, flags, mods.get("DEFINE"), "EXACT" in mods),
                     source, mods.get("AT"), mods.get("THRESHOLD"), "MENTIONS" in mods)

    def decision(self):
        kind = self.take().value.upper()
        if kind == "NOUL":
            prop = self.expect("STRING", "a proposition string").value
            opts, descs = (prop,), ("",)
        else:
            opts, descs = self.options()
        mods = self.modifiers()
        source = self.from_()
        mods = self.modifiers(mods)
        return Decision(kind, opts, descs, source, mods.get("ASK", ""), mods.get("THRESHOLD"))

    def options(self) -> Tuple[tuple, tuple]:
        if self.tok.kind == "[":
            self.take()
            labels = []
            while self.tok.kind != "]":
                labels.append(self.expect("STRING", "an option string").value)
                if self.tok.kind == ",":
                    self.take()
                elif self.tok.kind != "]":
                    _err("expected ',' or ']'", self.src, self.tok.pos)
            self.take()
            return tuple(labels), tuple("" for _ in labels)
        if self.tok.kind == "{":
            self.take()
            labels, descs = [], []
            while self.tok.kind != "}":
                k = self.tok
                if k.kind not in ("STRING", "WORD"):
                    _err("expected an option label", self.src, k.pos)
                self.take()
                self.expect(":", "':'")
                labels.append(k.value)
                descs.append(self.expect("STRING", "an option description").value)
                if self.tok.kind == ",":
                    self.take()
                elif self.tok.kind != "}":
                    _err("expected ',' or '}'", self.src, self.tok.pos)
            self.take()
            return tuple(labels), tuple(descs)
        _err("expected options: [\"a\", \"b\"] or {\"a\": \"description\"}", self.src, self.tok.pos)

    def from_(self) -> Source:
        if not self.kw("FROM"):
            _err("expected FROM", self.src, self.tok.pos)
        self.take()
        t = self.tok
        if t.kind == "STRING":
            self.take()
            return Source(literal=t.value)
        if t.kind == "WORD":
            self.take()
            return Source(name=t.value)
        _err("expected a source string or name after FROM", self.src, t.pos)

    def modifiers(self, m=None) -> dict:
        m = dict(m or {})
        while self.kw(*MODIFIERS):
            k = self.take().value.upper()
            if k in m:
                _err(f"duplicate modifier {k}", self.src, self.tok.pos)
            if k in ("EXACT", "MENTIONS"):
                m[k] = True
            elif k == "AT":
                v = self.expect("NUMBER", "an integer index").value
                if not isinstance(v, int) or v == 0:
                    _err("AT needs a non-zero integer (1-based; negative counts from the end)", self.src, self.tok.pos)
                m[k] = v
            elif k == "THRESHOLD":
                v = self.expect("NUMBER", "a probability").value
                if not 0 <= v <= 1:
                    _err("THRESHOLD must be in [0, 1]", self.src, self.tok.pos)
                m[k] = float(v)
            else:
                m[k] = self.expect("STRING", f"a string after {k}").value
        return m


def parse(src: str) -> Program:
    if not isinstance(src, str) or not src.strip():
        raise DjorgeError("PARSE_ERROR", "empty query")
    return Parser(src).program()
