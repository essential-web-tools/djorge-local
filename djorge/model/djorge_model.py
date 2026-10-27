"""DJORGE-Qwen: a Qwen-derived semantic resolution / decision model.

        rows (query-first serialization, control tokens as learned embeddings)
          │
   Qwen decoder (causal)  ◄── LoRA "djorge" adapter: the backbone itself is trained
          │                    (+ optional tenant adapters, switchable per request)
   learned layer mix over {L/2, 3L/4, L} hidden states
          │
   Semantic Adapter: projection + role embeddings + 2 bidirectional layers
          │                (adds right-context that a causal decoder lacks)
   Universal Decision Core readouts (operation is in the sequence, readouts shared):
     token membership + mention-begin  -> spans  (CLASS / ENTITY resolution)
     option slot scores                -> CHOICE softmax | SET sigmoids | RANK PL utilities
     cumulative-link over option slots -> SCORE (rank-consistent ordinal)
     end-token binary                  -> NOUL
No LM head, no generate(): one forward pass per row, rows batched.
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

import torch
import torch.nn as nn
import torch.nn.functional as F

from . import lora
from .serialize import SPECIALS, Row


class DjorgeModel(nn.Module):
    def __init__(self, backbone: nn.Module, d: int = 512, adapter_layers: int = 2, heads: int = 8,
                 lora_r: int = 16, lora_alpha: float = 32, use_lora: bool = True, lora_targets=None, rslora: bool = False):
        super().__init__()
        self.backbone = backbone
        H = backbone.config.hidden_size
        L = backbone.config.num_hidden_layers
        self.cfg = dict(d=d, adapter_layers=adapter_layers, heads=heads, lora_r=lora_r, lora_alpha=lora_alpha,
                        use_lora=use_lora, hidden=H, layers=L,
                        lora_targets=list(lora_targets or lora.TARGETS), rslora=rslora)
        for p in backbone.parameters():
            p.requires_grad = False
        if use_lora:
            lora.inject(backbone, "djorge", r=lora_r, alpha=lora_alpha, targets=tuple(lora_targets or lora.TARGETS), rslora=rslora)
            lora.set_active(backbone, ["djorge"])
        emb = backbone.get_input_embeddings().weight
        self.special = nn.Parameter(emb.detach().float().mean(0, keepdim=True).repeat(len(SPECIALS), 1)
                                    + 0.02 * torch.randn(len(SPECIALS), H, device=emb.device))
        self.mix_layers = sorted({max(1, L // 2), max(1, (3 * L) // 4), L})
        self.mix = nn.Parameter(torch.zeros(len(self.mix_layers)))
        self.proj = nn.Linear(H, d)
        self.role = nn.Embedding(4, d)
        layer = nn.TransformerEncoderLayer(d, heads, 4 * d, 0.1, batch_first=True, norm_first=True, activation="gelu")
        self.adapter = nn.TransformerEncoder(layer, adapter_layers, enable_nested_tensor=False)
        self.ln = nn.LayerNorm(d)
        mlp = lambda i, o=1: nn.Sequential(nn.Linear(i, d), nn.GELU(), nn.Linear(d, o))
        self.tok_head = mlp(3 * d, 4)          # [membership, begin, negated, nonfactual]
        self.slot_head = mlp(2 * d)
        self.bin_head = nn.Linear(d, 1)
        self.ord_eta = nn.Linear(d, 1)
        self.ord_b0 = nn.Linear(d, 1)
        self.ord_delta = mlp(2 * d)

    # ------------------------------------------------------------ encoding --
    def collate(self, rows: List[Row], device):
        T = max(len(r.ids) for r in rows)
        ids = torch.zeros(len(rows), T, dtype=torch.long)
        roles = torch.zeros(len(rows), T, dtype=torch.long)
        mask = torch.zeros(len(rows), T, dtype=torch.bool)
        for i, r in enumerate(rows):
            ids[i, :len(r.ids)] = torch.tensor(r.ids)
            roles[i, :len(r.ids)] = torch.tensor(r.roles)
            mask[i, :len(r.ids)] = True
        return ids.to(device), roles.to(device), mask.to(device)

    def encode(self, ids, roles, mask):
        """One Qwen forward for a batch of rows -> adapter states [B, T, d]."""
        emb_layer = self.backbone.get_input_embeddings()
        base = emb_layer(ids.clamp(min=0))
        sp = ids < 0
        if sp.any():
            base = base.clone()
            base[sp] = self.special[(-ids[sp] - 1)].to(base.dtype)
        out = self.backbone(inputs_embeds=base, attention_mask=mask.long(), output_hidden_states=True,
                            use_cache=False)
        hs = out.hidden_states
        w = torch.softmax(self.mix, 0)
        h = sum(w[i] * hs[l].float() for i, l in enumerate(self.mix_layers))
        x = self.proj(h) + self.role(roles)
        x = self.adapter(x, src_key_padding_mask=~mask)
        return self.ln(x)

    # ------------------------------------------------------------ readouts --
    def token_logits(self, X, rows: List[Row]):
        """List of [n_src, 4] tensors (membership, begin, negated, nonfactual) per row."""
        out = []
        for i, r in enumerate(rows):
            e = X[i, r.end_idx]
            s = X[i, r.src_tok_idx]
            ee = e.expand_as(s)
            out.append(self.tok_head(torch.cat([s, ee, s * ee], -1)))
        return out

    def option_vectors(self, X, i, r: Row):
        return torch.stack([X[i, a:b].mean(0) for a, b in r.opt_spans])

    def slot_logits(self, X, i, r: Row):
        o = self.option_vectors(X, i, r)
        e = X[i, r.end_idx].expand_as(o)
        return self.slot_head(torch.cat([o, e], -1)).squeeze(-1)

    def binary_logit(self, X, i, r: Row):
        return self.bin_head(X[i, r.end_idx]).squeeze(-1)

    def ordinal_logprobs(self, X, i, r: Row):
        o = self.option_vectors(X, i, r)                          # [K, d]
        e = X[i, r.end_idx]
        eta = self.ord_eta(e)                                     # [1]
        b0 = self.ord_b0(o[0])                                    # [1]
        if o.size(0) > 2:
            delta = F.softplus(self.ord_delta(torch.cat([o[1:-1], o[2:]], -1)).squeeze(-1)) + 1e-3
            b = torch.cat([b0, b0 + torch.cumsum(delta, 0)])
        else:
            b = b0
        gt = torch.sigmoid(eta - b)                               # P(y > k), decreasing
        cum = torch.cat([gt.new_ones(1), gt, gt.new_zeros(1)])
        p = (cum[:-1] - cum[1:]).clamp(min=1e-6)
        return torch.log(p / p.sum())

    # ---------------------------------------------------------- artifacts --
    def trainable_state(self) -> Dict[str, torch.Tensor]:
        """Everything DJORGE adds on top of the frozen base: decision core + special
        embeddings + layer mix (LoRA saved separately as the adapter artifact)."""
        return {k: v.detach().cpu() for k, v in self.state_dict().items() if not k.startswith("backbone.")}

    def save(self, path: str, metadata: dict, calibration_json: Optional[str] = None, merged: bool = False):
        from safetensors.torch import save_file
        for sub in ("adapter", "decision", "calibration", "metadata"):
            os.makedirs(os.path.join(path, sub), exist_ok=True)
        save_file({k: v.contiguous() for k, v in self.trainable_state().items()},
                  os.path.join(path, "decision", "core.safetensors"))
        if self.cfg["use_lora"]:
            save_file({k: v.contiguous() for k, v in lora.adapter_state(self.backbone, "djorge").items()},
                      os.path.join(path, "adapter", "djorge_lora.safetensors"))
        if calibration_json:
            open(os.path.join(path, "calibration", "calibration.json"), "w").write(calibration_json)
        meta = {**metadata, "model_config": self.cfg}
        json.dump(meta, open(os.path.join(path, "metadata", "djorge.json"), "w"), indent=2)
        if merged:                        # standalone Qwen-architecture derivative checkpoint
            os.makedirs(os.path.join(path, "merged"), exist_ok=True)
            save_file({k: v.contiguous() for k, v in lora.merged_state_dict(self.backbone).items()},
                      os.path.join(path, "merged", "model.safetensors"))
            self.backbone.config.save_pretrained(os.path.join(path, "merged"))

    def load_parts(self, path: str):
        from safetensors.torch import load_file
        missing = self.load_state_dict(load_file(os.path.join(path, "decision", "core.safetensors")), strict=False)
        ap = os.path.join(path, "adapter", "djorge_lora.safetensors")
        if self.cfg["use_lora"] and os.path.exists(ap):
            lora.load_adapter(self.backbone, "djorge", load_file(ap), self.cfg["lora_alpha"] / self.cfg["lora_r"])
        return missing
