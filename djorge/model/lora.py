"""Minimal, dependency-free LoRA with named multi-adapter support.

  * base Linear weights stay frozen and untouched (base Qwen recoverable)
  * several adapters can coexist ("djorge" = task adaptation, "tenant:<id>" =
    per-tenant personalization); the active set is chosen per forward
  * `merge()` folds active adapters into a COPY of the weights for export of a
    standalone Qwen-derived checkpoint
"""
from __future__ import annotations

import math
from contextlib import contextmanager
from typing import Dict, Iterable, List, Optional

import torch
import torch.nn as nn

TARGETS = ("q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj")


class LoRALinear(nn.Module):
    def __init__(self, base: nn.Linear):
        super().__init__()
        self.base = base
        for p in self.base.parameters():
            p.requires_grad = False
        self.A = nn.ParameterDict()
        self.B = nn.ParameterDict()
        self.scale: Dict[str, float] = {}
        self.active: List[str] = []

    def add(self, name: str, r: int, alpha: float, dropout: float = 0.0, rslora: bool = False):
        key = name.replace(":", "__").replace(".", "_")
        dev, dt = self.base.weight.device, self.base.weight.dtype
        a = nn.Parameter(torch.empty(r, self.base.in_features, device=dev, dtype=torch.float32))
        nn.init.kaiming_uniform_(a, a=math.sqrt(5))
        self.A[key] = a
        self.B[key] = nn.Parameter(torch.zeros(self.base.out_features, r, device=dev, dtype=torch.float32))
        self.scale[key] = alpha / (r ** 0.5) if rslora else alpha / r   # rsLoRA: stable scaling at higher ranks

    def forward(self, x):
        y = self.base(x)
        for k in self.active:
            if k in self.A:
                y = y + ((x.float() @ self.A[k].t()) @ self.B[k].t() * self.scale[k]).to(y.dtype)
        return y


def _key(name):
    return name.replace(":", "__").replace(".", "_")


def inject(model: nn.Module, name: str, r: int = 16, alpha: float = 32, targets: Iterable[str] = TARGETS,
           rslora: bool = False) -> int:
    """Wrap target Linear layers (once) and add adapter `name`. Returns #trainable params added."""
    targets = tuple(targets)
    added = 0
    for parent in list(model.modules()):
        for child_name, child in list(parent.named_children()):
            if child_name in targets and isinstance(child, (nn.Linear, LoRALinear)):
                if isinstance(child, nn.Linear):
                    child = LoRALinear(child)
                    setattr(parent, child_name, child)
                child.add(name, r, alpha, rslora=rslora)
                added += child.A[_key(name)].numel() + child.B[_key(name)].numel()
    return added


def lora_modules(model) -> List[LoRALinear]:
    return [m for m in model.modules() if isinstance(m, LoRALinear)]


def set_active(model, names: Iterable[str]):
    keys = [_key(n) for n in names]
    for m in lora_modules(model):
        m.active = [k for k in keys if k in m.A]


@contextmanager
def disabled(model):
    """Temporarily run the untouched base model (teacher pass for preservation loss)."""
    saved = [(m, m.active) for m in lora_modules(model)]
    for m, _ in saved:
        m.active = []
    try:
        yield
    finally:
        for m, a in saved:
            m.active = a


def adapter_state(model, name) -> Dict[str, torch.Tensor]:
    k = _key(name)
    out = {}
    for mod_name, m in model.named_modules():
        if isinstance(m, LoRALinear) and k in m.A:
            out[f"{mod_name}.A"] = m.A[k].detach().cpu()
            out[f"{mod_name}.B"] = m.B[k].detach().cpu()
    return out


def load_adapter(model, name, state: Dict[str, torch.Tensor], alpha_over_r: float):
    k = _key(name)
    mods = dict(model.named_modules())
    for key, t in state.items():
        mod_name, which = key.rsplit(".", 1)
        m = mods[mod_name]
        if not isinstance(m, LoRALinear):
            raise ValueError(f"{mod_name} is not LoRA-wrapped")
        if k not in m.A:
            r = t.shape[0] if which == "A" else t.shape[1]
            m.add(name, r, alpha_over_r * r)
        getattr(m, which)[k].data.copy_(t.to(m.base.weight.device))


def adapter_param_groups(model, name):
    """(A params, B params) - LoRA+ trains B with a larger learning rate than A."""
    k = _key(name)
    a, b = [], []
    for m in lora_modules(model):
        if k in m.A:
            a.append(m.A[k]); b.append(m.B[k])
    return a, b


def adapter_params(model, name) -> List[nn.Parameter]:
    k = _key(name)
    ps = []
    for m in lora_modules(model):
        if k in m.A:
            ps += [m.A[k], m.B[k]]
    return ps


@torch.no_grad()
def merged_state_dict(model) -> Dict[str, torch.Tensor]:
    """State dict of the backbone with ACTIVE adapters folded in and LoRA wrappers
    removed: a standalone Qwen-architecture checkpoint (the derivative model)."""
    sd = {}
    for name, mod in model.named_modules():
        if isinstance(mod, LoRALinear):
            W = mod.base.weight.float().clone()
            for k in mod.active:
                W += (mod.B[k] @ mod.A[k]) * mod.scale[k]
            sd[f"{name}.weight"] = W.to(mod.base.weight.dtype)
            if mod.base.bias is not None:
                sd[f"{name}.bias"] = mod.base.bias.clone()
    for name, t in model.state_dict().items():          # persistent tensors only
        if ".base." in name or ".A." in name or ".B." in name:
            continue
        sd.setdefault(name, t.detach().clone())
    return sd
