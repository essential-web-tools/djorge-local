"""Lossless-in-practice memory savings for serving (no retraining).

Int8Embedding: the Qwen3-0.6B token table is 151,936 x 1,024 = 155M parameters (~26% of the model) but it is only
ever READ by row lookup - no matmul. Storing it as int8 with one scale per row costs 1 byte/param instead of 4 (fp32)
or 2 (bf16). The reconstruction error per row is <= scale/2 (~0.4% of the row's max), far below what a frozen
encoder's first layer norm can see. Verify with benchmarks/serve_bench.py (dev accuracy must not change).
"""
from __future__ import annotations

import torch
import torch.nn as nn


class Int8Embedding(nn.Module):
    def __init__(self, weight: torch.Tensor, out_dtype: torch.dtype = None):
        super().__init__()
        w = weight.detach().float()
        scale = w.abs().amax(dim=1, keepdim=True).clamp(min=1e-8) / 127.0
        self.register_buffer("q", torch.round(w / scale).clamp(-127, 127).to(torch.int8))
        self.register_buffer("scale", scale.to(torch.float32))
        self.out_dtype = out_dtype or weight.dtype
        self.num_embeddings, self.embedding_dim = w.shape
        self.padding_idx = None

    def forward(self, ids: torch.Tensor) -> torch.Tensor:
        return (self.q[ids].to(torch.float32) * self.scale[ids]).to(self.out_dtype)

    @property
    def weight(self):                      # read-only dequantized view (only used at model construction time)
        return self.q.to(torch.float32) * self.scale


def compress_embeddings(backbone: nn.Module) -> dict:
    emb = backbone.get_input_embeddings()
    if isinstance(emb, Int8Embedding):
        return {"embed_int8": True, "saved_mb": 0.0}
    if isinstance(emb, PrunedEmbedding):
        before = emb.table.numel() * emb.table.element_size()
        emb.to_int8()
        return {"embed_int8": True, "saved_mb": round((before - emb.q.numel() - emb.scale.numel() * 4) / 1e6, 1)}
    before = emb.weight.numel() * emb.weight.element_size()
    new = Int8Embedding(emb.weight.data, out_dtype=emb.weight.dtype).to(emb.weight.device)
    backbone.set_input_embeddings(new)
    del emb
    after = new.q.numel() + new.scale.numel() * 4
    return {"embed_int8": True, "saved_mb": round((before - after) / 1e6, 1)}


class PrunedEmbedding(nn.Module):
    """Token table restricted to the tokens the model can actually meet (e.g. Latin-script text, URLs, digits).

    The tokenizer is unchanged; a small int32 map sends every kept id to its row and every dropped id to ONE fallback
    row (mean of the dropped rows). Kept tokens give bit-identical vectors, so on texts made of kept tokens the model
    output does not change at all. Optionally stores the table in int8 (one scale per row)."""

    def __init__(self, weight: torch.Tensor, keep_ids, out_dtype: torch.dtype = None):
        super().__init__()
        w = weight.detach()
        V = w.shape[0]
        keep = torch.as_tensor(sorted(set(int(i) for i in keep_ids if 0 <= int(i) < V)), dtype=torch.long)
        mask = torch.ones(V, dtype=torch.bool)
        mask[keep] = False
        fallback = w[mask].float().mean(0, keepdim=True) if mask.any() else torch.zeros(1, w.shape[1])
        table = torch.cat([w[keep].float(), fallback], 0)
        idx = torch.full((V,), len(keep), dtype=torch.int32)
        idx[keep] = torch.arange(len(keep), dtype=torch.int32)
        self.register_buffer("idx", idx)
        self.register_buffer("table", table.to(w.dtype))
        self.q = None
        self.out_dtype = out_dtype or w.dtype
        self.num_embeddings, self.embedding_dim = V, w.shape[1]
        self.kept = len(keep)
        self.padding_idx = None

    def to_int8(self):
        t = self.table.float()
        scale = t.abs().amax(dim=1, keepdim=True).clamp(min=1e-8) / 127.0
        self.q = torch.round(t / scale).clamp(-127, 127).to(torch.int8)
        self.scale = scale
        del self.table
        self.register_buffer("table", torch.empty(0))
        return self

    def forward(self, ids: torch.Tensor) -> torch.Tensor:
        rows = self.idx[ids].long()
        if self.q is not None:
            return (self.q[rows].float() * self.scale[rows]).to(self.out_dtype)
        return self.table[rows].to(self.out_dtype)

    @property
    def weight(self):                      # dequantized view over the full vocabulary (construction time only)
        rows = self.idx.long()
        return (self.q[rows].float() * self.scale[rows]) if self.q is not None else self.table[rows]


def prune_vocab(backbone: nn.Module, keep_ids) -> dict:
    emb = backbone.get_input_embeddings()
    before = emb.weight.numel() * emb.weight.element_size()
    new = PrunedEmbedding(emb.weight.data, keep_ids).to(emb.weight.device)
    backbone.set_input_embeddings(new)
    del emb
    after = new.table.numel() * new.table.element_size() + new.idx.numel() * 4
    return {"vocab_kept": new.kept, "vocab_total": new.num_embeddings, "saved_mb": round((before - after) / 1e6, 1)}
