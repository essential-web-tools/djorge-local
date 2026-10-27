"""Strictly proper training objectives (direct optimization of proper scoring
rules - the exact-gradient counterpart of RL-for-calibrated-decisions)."""
from __future__ import annotations

import torch
import torch.nn.functional as F


def token_targets(row, spans):
    n = len(row.src_offsets)
    mem = torch.zeros(n)
    beg = torch.zeros(n)
    for a, b in spans:
        first = True
        for t, (s, e) in enumerate(row.src_offsets):
            if e > s and s < b and a < e:
                mem[t] = 1.0
                if first:
                    beg[t] = 1.0
                    first = False
    return mem, beg


def resolve_loss(tl, mem, beg, pos_weight=3.0):
    z_m, z_b = tl[:, 0], tl[:, 1]
    lm = F.binary_cross_entropy_with_logits(z_m, mem, pos_weight=torch.tensor(pos_weight, device=z_m.device))
    if mem.sum() > 0:
        sel = mem > 0
        lb = F.binary_cross_entropy_with_logits(z_b[sel], beg[sel])
    else:
        lb = z_b.sum() * 0
    return lm + 0.5 * lb


def choice_loss(z, y, lam=0.5):
    lp = F.log_softmax(z, -1)
    oh = torch.zeros_like(lp); oh[y] = 1
    return -lp[y] + lam * ((lp.exp() - oh) ** 2).sum()


def noul_loss(z, y, lam=0.5):
    t = torch.tensor(float(y), device=z.device)
    return F.binary_cross_entropy_with_logits(z, t) + lam * (torch.sigmoid(z) - t) ** 2


def ordinal_loss(lp, y, lam=0.5):
    oh = torch.zeros_like(lp); oh[y] = 1
    rps = ((torch.cumsum(lp.exp(), 0) - torch.cumsum(oh, 0))[:-1] ** 2).sum()
    return -lp[y] + lam * rps


def set_loss(z, ys):
    return F.binary_cross_entropy_with_logits(z, ys)


def rank_loss(u, order):
    u = u[order]
    return -sum(u[t] - torch.logsumexp(u[t:], 0) for t in range(len(order) - 1))


def polarity_loss(tl, row, spans, pols):
    """BCE on the negated / nonfactual heads over gold mention tokens."""
    import torch as _t
    idx, yn, yf = [], [], []
    for (a, b), pol in zip(spans, pols):
        for t, (s, e) in enumerate(row.src_offsets):
            if e > s and s < b and a < e:
                idx.append(t); yn.append(float(pol == "negated")); yf.append(float(pol == "nonfactual"))
    if not idx:
        return tl.sum() * 0
    i = _t.tensor(idx, device=tl.device)
    return (F.binary_cross_entropy_with_logits(tl[i, 2], _t.tensor(yn, device=tl.device)) +
            F.binary_cross_entropy_with_logits(tl[i, 3], _t.tensor(yf, device=tl.device)))
