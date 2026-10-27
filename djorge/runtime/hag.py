"""HAG - Hierarchical Aggregation and Grounding for long documents (extractive; nothing is generated).

Inspired by RAPTOR (Sarthi et al., ICLR 2024: cluster chunks, summarise each cluster, retrieve across levels).
Origo does not generate text, so every level here is EXTRACTIVE and keeps character offsets:

  level 0  sentences            exact offsets in the original document
  level 1  chunks               consecutive sentences up to ~max_chars, 1 sentence of overlap
  level 2  sections             headings when present, otherwise groups of chunks; each section is represented by
                                its most central ORIGINAL sentences (TF-IDF centroid) + the facts extracted from it
  facts    entities/quantities  produced by a pluggable extractor (Origo's span heads in the server), consolidated
                                across the document: same (label, normalised text) -> one entity with all
                                occurrences; different explicit quantities for the same entity -> reported as
                                `divergences` (never silently merged)
  search   BM25 over chunks + boost for chunks that contain entities named in the query

Window size and hierarchy are separate things: the model still reads one chunk at a time (its own window); the
hierarchy decides WHICH chunks it reads and how their results are combined.
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter, defaultdict
from typing import Callable, Dict, List, Optional

STOP = set("""a o e é de da do das dos em um uma uns umas para por com sem que se na no nas nos ao aos à às ou
mas como mais menos muito pouco já não sim seu sua seus suas ele ela eles elas isso isto esse essa este esta foi
ser são era the of and to in is was for on with as by at an be this that it from or are""".split())
MAX_DOC_CHARS, MAX_CHUNKS = 400_000, 800


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.casefold())
    return "".join(c for c in s if not unicodedata.combining(c))


def _terms(s: str) -> List[str]:
    return [w for w in re.findall(r"[^\W_]+", _fold(s)) if len(w) > 2 and w not in STOP]


def sentences(text: str) -> List[dict]:
    """Sentence spans with exact offsets. Lines are split first (headings stay whole), then sentences
    (abbreviations such as 'Sr.', 'Dr.' and enumerations such as '1.' are not sentence ends)."""
    out = []
    end_rx = re.compile(r"(?<!\bSr)(?<!\bSra)(?<!\bDr)(?<!\bDra)(?<!\bex)(?<!\bp)(?<![A-Z])(?<!^\d)(?<!^\d\d)"
                        r"[.!?…]+[\"')\]]?(?=\s+|$)")
    for lm in re.finditer(r"[^\n]+", text):
        line, base = lm.group(), lm.start()
        if not line.strip():
            continue
        if _heading(line):
            a = base + len(line) - len(line.lstrip())
            out.append({"start": a, "end": a + len(line.strip()), "text": line.strip(), "heading": True})
            continue
        start = 0
        for m in end_rx.finditer(line):
            seg = line[start:m.end()]
            if seg.strip():
                a = base + start + len(seg) - len(seg.lstrip())
                out.append({"start": a, "end": a + len(seg.strip()), "text": seg.strip()})
            start = m.end()
        seg = line[start:]
        if seg.strip():
            a = base + start + len(seg) - len(seg.lstrip())
            out.append({"start": a, "end": a + len(seg.strip()), "text": seg.strip()})
    return out


def chunks(sents: List[dict], max_chars: int = 700) -> List[dict]:
    out, cur = [], []
    for s in sents:
        if cur and (cur[-1]["end"] - cur[0]["start"]) + len(s["text"]) > max_chars:
            out.append(cur)
            cur = cur[-1:] if len(cur) > 1 else []       # 1 sentence overlap
        cur.append(s)
    if cur:
        out.append(cur)
    return [{"id": i, "start": c[0]["start"], "end": c[-1]["end"], "sentences": c} for i, c in enumerate(out)]


def _heading(line: str) -> bool:
    l = line.strip()
    return bool(l) and len(l) < 90 and (l.startswith("#") or re.match(r"^(\d+(\.\d+)*[.)]?|[IVX]+[.)])\s+\S", l)
                                         or (l.isupper() and len(l.split()) <= 10))


class BM25:
    def __init__(self, docs: List[List[str]], k1=1.4, b=0.75):
        self.docs, self.k1, self.b = docs, k1, b
        self.n = len(docs)
        self.avg = sum(len(d) for d in docs) / max(1, self.n)
        self.df = Counter(t for d in docs for t in set(d))
        self.tf = [Counter(d) for d in docs]

    def score(self, q: List[str]) -> List[float]:
        out = []
        for i, tf in enumerate(self.tf):
            s, L = 0.0, len(self.docs[i]) or 1
            for t in q:
                if t not in tf:
                    continue
                idf = math.log(1 + (self.n - self.df[t] + 0.5) / (self.df[t] + 0.5))
                s += idf * tf[t] * (self.k1 + 1) / (tf[t] + self.k1 * (1 - self.b + self.b * L / self.avg))
            out.append(s)
        return out


class Document:
    def __init__(self, text: str, max_chars: int = 700, extractor: Optional[Callable[[str], List[dict]]] = None):
        if len(text) > MAX_DOC_CHARS:
            raise ValueError(f"document longer than {MAX_DOC_CHARS} characters")
        self.text = text
        self.sents = sentences(text)
        self.chunks = chunks(self.sents, max_chars)
        if len(self.chunks) > MAX_CHUNKS:
            raise ValueError(f"document produces more than {MAX_CHUNKS} chunks")
        self.bm25 = BM25([_terms(text[c["start"]:c["end"]]) for c in self.chunks])
        self.sections = self._sections()
        self.facts: List[dict] = []
        if extractor:
            self.extract(extractor)

    # ── level 2 ──
    def _sections(self, per=6):
        heads = [s for s in self.sents if s.get("heading")]
        bounds = [h["start"] for h in heads]
        if len(bounds) >= 2:
            bounds = sorted(set([0] + bounds)) + [len(self.text)]
        else:
            bounds = [c["start"] for c in self.chunks[::per]] + [len(self.text)]
        secs = []
        for i in range(len(bounds) - 1):
            a, b = bounds[i], bounds[i + 1]
            ss = [s for s in self.sents if a <= s["start"] < b]
            if not ss:
                continue
            title = ss[0]["text"] if ss[0].get("heading") else None
            secs.append({"id": len(secs), "start": ss[0]["start"], "end": ss[-1]["end"], "title": title,
                         "key_sentences": self._central([x for x in ss if not x.get("heading")] or ss), "chunks": [c["id"] for c in self.chunks
                                                                         if c["start"] < ss[-1]["end"] and c["end"] > ss[0]["start"]]})
        return secs

    def _central(self, ss: List[dict], k: int = 3) -> List[dict]:
        """Most central ORIGINAL sentences (cosine to the section's TF-IDF centroid)."""
        vecs = [Counter(_terms(s["text"])) for s in ss]
        df = Counter(t for v in vecs for t in v)
        n = len(vecs)
        idf = {t: math.log(1 + n / df[t]) for t in df}
        cen = Counter()
        for v in vecs:
            for t, c in v.items():
                cen[t] += c * idf[t]
        def cos(v):
            num = sum(c * idf[t] * cen[t] for t, c in v.items())
            den = math.sqrt(sum((c * idf[t]) ** 2 for t, c in v.items())) * math.sqrt(sum(x * x for x in cen.values()))
            return num / den if den else 0.0
        best = sorted(range(n), key=lambda i: -cos(vecs[i]))[:k]
        return [{"start": ss[i]["start"], "end": ss[i]["end"], "text": ss[i]["text"]} for i in sorted(best)]

    # ── facts ──
    def extract(self, extractor: Callable[[str], List[dict]]):
        """extractor(chunk_text) -> [{label, text, start, end, probability?, polarity?, quantity?}] (chunk offsets)."""
        seen = set()
        for c in self.chunks:
            piece = self.text[c["start"]:c["end"]]
            for f in extractor(piece) or []:
                a, b = c["start"] + int(f["start"]), c["start"] + int(f["end"])
                if not (0 <= a < b <= len(self.text)) or self.text[a:b] != f["text"]:
                    continue                                       # invalid offsets are dropped, never repaired
                key = (f.get("label"), a, b)
                if key in seen:                                    # overlap between chunks
                    continue
                seen.add(key)
                self.facts.append(dict(f, start=a, end=b, chunk=c["id"]))
        return self.facts

    def entities(self) -> List[dict]:
        groups: Dict[tuple, dict] = {}
        for f in self.facts:
            k = (f.get("label"), _fold(f["text"]).strip())
            g = groups.setdefault(k, {"label": f.get("label"), "text": f["text"], "occurrences": [],
                                      "quantities": set(), "polarities": Counter()})
            g["occurrences"].append({"start": f["start"], "end": f["end"], "chunk": f["chunk"],
                                     "probability": f.get("probability")})
            if f.get("quantity") is not None:
                g["quantities"].add(f["quantity"])
            g["polarities"][f.get("polarity", "asserted")] += 1
        out = []
        for g in groups.values():
            q = sorted(g.pop("quantities"))
            g["quantities"] = q
            g["polarities"] = dict(g["polarities"])
            g["count"] = len(g["occurrences"])
            if len(q) > 1:
                g["divergence"] = f"quantidades diferentes ao longo do documento: {q}"
            out.append(g)
        return sorted(out, key=lambda g: (-g["count"], g["occurrences"][0]["start"]))

    # ── search ──
    def search(self, query: str, k: int = 4) -> List[dict]:
        q = _terms(query)
        scores = self.bm25.score(q)
        qents = {_fold(f["text"]) for f in self.facts if _fold(f["text"]) in _fold(query)}
        for f in self.facts:
            if _fold(f["text"]) in qents:
                scores[f["chunk"]] += 1.0
        top = sorted(range(len(scores)), key=lambda i: -scores[i])[:k]
        return [{"chunk": i, "start": self.chunks[i]["start"], "end": self.chunks[i]["end"],
                 "score": round(scores[i], 4), "text": self.text[self.chunks[i]["start"]:self.chunks[i]["end"]]}
                for i in top if scores[i] > 0]

    def tree(self, with_text=False) -> dict:
        return {"chars": len(self.text), "sentences": len(self.sents), "chunks": len(self.chunks),
                "sections": [{k: v for k, v in s.items()} for s in self.sections],
                "entities": self.entities()}
