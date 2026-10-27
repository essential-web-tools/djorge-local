"""Query representation for the Qwen backbone (NOT a chat prompt).

Layout of one row (query-first, so every source token is conditioned on the
query through all causal layers; options last, so every option is conditioned
on query AND source):

  <DJORGE> <OP:x> [<MODE:m> target [<DEF> definition]] [<ASK> question] <SRC> source... [<OPT> option]* <END>

Control tokens are NOT vocabulary strings. They are negative ids resolved by
ZorqModel to a small trainable embedding table, so user text can never forge a
control token (user text is tokenized with split_special_tokens=True as well).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

SPECIALS = ["DJORGE", "OP:RESOLVE", "OP:CHOICE", "OP:SCORE", "OP:NOUL", "OP:RANK", "OP:SET",
            "MODE:CLASS", "MODE:ENTITY", "DEF", "ASK", "SRC", "OPT", "END", "OP:ANSWER"]
SID = {s: -(i + 1) for i, s in enumerate(SPECIALS)}        # negative ids
ROLE_SPECIAL, ROLE_QUERY, ROLE_SOURCE, ROLE_OPTION = 0, 1, 2, 3


@dataclass
class Row:
    ids: List[int]
    roles: List[int]
    src_tok_idx: List[int]                     # positions of source tokens in ids
    src_offsets: List[Tuple[int, int]]         # char offsets (into the FULL source) per source token
    opt_spans: List[Tuple[int, int]]           # [start, end) positions of each option (incl. <OPT>)
    end_idx: int
    window: Tuple[int, int] = (0, 0)           # char range of the source covered by this row


class Serializer:
    def __init__(self, tokenizer, max_tokens: int = 512, stride: int = 384):
        self.tok, self.max_tokens, self.stride = tokenizer, max_tokens, stride

    def _enc(self, text: str, offsets=False):
        kw = dict(add_special_tokens=False)
        try:
            enc = self.tok(text, return_offsets_mapping=offsets, split_special_tokens=True, **kw)
        except TypeError:
            enc = self.tok(text, return_offsets_mapping=offsets, **kw)
        return enc["input_ids"], (enc["offset_mapping"] if offsets else None)

    def rows(self, op: str, source: str, mode: Optional[str] = None, target: str = "",
             definition: Optional[str] = None, ask: str = "", options: Sequence[str] = ()) -> List[Row]:
        head: List[int] = [SID["DJORGE"], SID["OP:" + op]]
        roles = [ROLE_SPECIAL, ROLE_SPECIAL]
        if mode:
            t, _ = self._enc(" " + target)
            head += [SID["MODE:" + mode]] + t
            roles += [ROLE_SPECIAL] + [ROLE_QUERY] * len(t)
            if definition:
                d, _ = self._enc(" " + definition[:600])
                head += [SID["DEF"]] + d
                roles += [ROLE_SPECIAL] + [ROLE_QUERY] * len(d)
        if ask:
            a, _ = self._enc(" " + ask[:400])
            head += [SID["ASK"]] + a
            roles += [ROLE_SPECIAL] + [ROLE_QUERY] * len(a)
        head.append(SID["SRC"])
        roles.append(ROLE_SPECIAL)
        tail: List[int] = []
        troles: List[int] = []
        rel_spans = []
        for o in options:
            oi, _ = self._enc(" " + o[:300])
            s = len(tail)
            tail += [SID["OPT"]] + oi
            troles += [ROLE_SPECIAL] + [ROLE_OPTION] * len(oi)
            rel_spans.append((s, len(tail)))
        tail.append(SID["END"])
        troles.append(ROLE_SPECIAL)
        sids, soffs = self._enc(source, offsets=True)
        room = self.max_tokens - len(head) - len(tail)
        if room < 16:
            raise ValueError("query/options leave no room for the source; raise max_tokens")
        windows = []
        if len(sids) <= room:
            windows.append((0, len(sids)))
        else:                                             # sliding windows over long sources
            step = max(1, min(self.stride, room - 16))
            s = 0
            while True:
                windows.append((s, min(s + room, len(sids))))
                if s + room >= len(sids):
                    break
                s += step
        out = []
        for a, b in windows:
            ids = head + sids[a:b] + tail
            rl = roles + [ROLE_SOURCE] * (b - a) + troles
            base = len(head)
            src_idx = list(range(base, base + b - a))
            tail0 = base + (b - a)
            out.append(Row(ids, rl, src_idx, [tuple(x) for x in soffs[a:b]],
                           [(tail0 + s, tail0 + e) for s, e in rel_spans], len(ids) - 1,
                           (soffs[a][0] if b > a else 0, soffs[b - 1][1] if b > a else 0)))
        return out
