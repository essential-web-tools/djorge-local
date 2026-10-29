"""DJORGE trainer: turns a Qwen checkpoint into a DJORGE-Qwen derivative.

Default recipe (stage "lora"): LoRA on all Qwen linear projections + special
control-token embeddings + layer mix + semantic adapter + decision core, with a
PRESERVATION loss that keeps adapted hidden states close to the untouched base
on plain text (teacher = the same weights with LoRA disabled, so no second model
copy is held in memory). Stage "frozen" (no LoRA) is kept as an ablation.

    python -m djorge.training.trainer --backbone Qwen/Qwen3-0.6B --out artifacts/djorge-qwen3-0.6b
    python -m djorge.training.trainer --backbone tiny --out artifacts/djorge-tiny     # CI / CPU mechanics
Restartable: --resume continues from <out>/checkpoint.pt.
"""
from __future__ import annotations

import argparse
import json
import io
import os
import random
import re
import time
from typing import List

import numpy as np
import torch
import torch.nn.functional as F

from ..model import lora
from ..model.backbone import load_backbone, local_bpe_tokenizer, pick_device, tiny_qwen3
from ..model.serialize import Serializer
from ..model.djorge_model import DjorgeModel
from ..version import DJORGE_VERSION
from . import losses as L
from .data import dataset_version, generate


def _pick(rows, r):
    """Long sources: train on the window that holds the evidence (r["focus"] = char offset), not blindly the first."""
    f = r.get("focus")
    if f is None or len(rows) <= 1:
        return rows[:1]
    for row in rows:
        if row.window[0] <= f < row.window[1]:
            return [row]
    return rows[:1]


def build_rows(ser, r, rng=None, shuffle=True):
    t = r["task"]
    if t == "resolve":
        return _pick(ser.rows("RESOLVE", r["source"], mode=r["mode"], target=r["target"], definition=r.get("definition")), r), None
    if t == "answer":
        return _pick(ser.rows("ANSWER", r["source"], ask=r["ask"]), r), None
    opts, descs = list(r["options"]), list(r.get("descriptions") or [""] * len(r["options"]))
    perm = list(range(len(opts)))
    if shuffle and rng is not None and t in ("CHOICE", "SET", "RANK"):
        rng.shuffle(perm)
    opts, descs = [opts[i] for i in perm], [descs[i] for i in perm]
    if t == "NOUL":
        return _pick(ser.rows("NOUL", r["source"], ask=opts[0]), r), perm
    texts = [f"{o}: {d}" if d else o for o, d in zip(opts, descs)]
    return _pick(ser.rows(t, r["source"], ask=r.get("ask", ""), options=texts), r), perm


def soft_loss(model, X, i, row, r, perm, dev, known=None):
    """Soft targets written by a LARGE teacher (djorge.sentinel.teacher): r["soft"] aligned with r["options"].
    SET: per-option probability; CHOICE / SCORE: distribution. Unknown SET labels are learned from the teacher only."""
    t = r["task"]
    q = torch.tensor([float(r["soft"][j]) for j in perm], device=dev).clamp(1e-4, 1 - 1e-4)
    if t == "SET":
        z = model.slot_logits(X, i, row).float()
        return F.binary_cross_entropy_with_logits(z, q)
    if t == "NOUL":
        return F.binary_cross_entropy_with_logits(model.binary_logit(X, i, row).float(), q[0])
    q = q / q.sum()
    lp = model.ordinal_logprobs(X, i, row).float() if t == "SCORE" else F.log_softmax(model.slot_logits(X, i, row).float(), -1)
    return F.kl_div(lp, q.log(), log_target=True, reduction="sum")


def record_loss(model, X, i, row, r, perm, tl, dev, soft_w=0.5):
    hard = _hard_loss(model, X, i, row, r, perm, tl, dev)
    if r.get("soft") is not None and r["task"] in ("SET", "CHOICE", "SCORE", "NOUL") and perm is not None:
        s = soft_loss(model, X, i, row, r, perm, dev)
        hard = s if hard is None else (1 - soft_w) * hard + soft_w * s
    if hard is None:
        return None
    return hard * float(r.get("weight", 1.0))


def _hard_loss(model, X, i, row, r, perm, tl, dev):
    t = r["task"]
    if t in ("resolve", "answer"):
        mem, beg = L.token_targets(row, r["spans"])
        loss = L.resolve_loss(tl, mem.to(dev), beg.to(dev))
        if t == "resolve" and r.get("polarity"):
            loss = loss + 0.5 * L.polarity_loss(tl, row, r["spans"], r["polarity"])
        return loss
    opts = [r["options"][j] for j in perm]
    if r.get("label") is None:
        return None
    if t == "NOUL":
        return L.noul_loss(model.binary_logit(X, i, row), r["label"])
    if t == "SCORE":
        return L.ordinal_loss(model.ordinal_logprobs(X, i, row), r["label"])
    z = model.slot_logits(X, i, row)
    if t == "CHOICE":
        return L.choice_loss(z, opts.index(r["label"]))
    if t == "SET":
        y = torch.tensor([1.0 if o in r["label"] else 0.0 for o in opts], device=dev)
        if r.get("known") is not None:                 # partial labels: only options the source really annotates
            m = torch.tensor([bool(r["known"][j]) for j in perm], device=dev)
            if not m.any():
                return None
            return F.binary_cross_entropy_with_logits(z[m], y[m])
        return L.set_loss(z, y)
    if t == "RANK":
        return L.rank_loss(z, [opts.index(o) for o in r["label"]])
    raise ValueError(t)


class _FakeQuantLinear(torch.nn.Module):
    """Wrap an nn.Linear so its weight is fake-quantized (int8, per output channel, symmetric) in the forward
    pass, with a straight-through gradient. Training through this teaches weights that survive real int8 at serving.
    `.unwrap()` returns the plain nn.Linear with the (unquantized) learned weight, for a clean standalone export."""

    act_levels = 127                       # dynamic activations: quint8 with reduce_range on x86 (0..127)

    def __init__(self, lin):
        super().__init__()
        self.lin = lin

    def forward(self, x):
        w = self.lin.weight
        scale = (w.detach().abs().amax(dim=1, keepdim=True) / 127.0).clamp(min=1e-8)
        wq = (w / scale).round().clamp(-127, 127) * scale
        wq = w + (wq - w).detach()
        # simulate the serving runtime's DYNAMIC activation quantization (per tensor, asymmetric), which caused most
        # of the int8 error measured on CPU
        lo, hi = x.detach().amin(), x.detach().amax()
        s_a = ((hi - lo) / self.act_levels).clamp(min=1e-8)
        xq = ((x - lo) / s_a).round().clamp(0, self.act_levels) * s_a + lo
        x = x + (xq - x).detach()
        return torch.nn.functional.linear(x, wq, self.lin.bias)

    def unwrap(self):
        return self.lin


def _wrap_fake_quant(module):
    for name, child in list(module.named_children()):
        if isinstance(child, torch.nn.Linear):
            setattr(module, name, _FakeQuantLinear(child))
        else:
            _wrap_fake_quant(child)


def _unwrap_fake_quant(module):
    for name, child in list(module.named_children()):
        if isinstance(child, _FakeQuantLinear):
            setattr(module, name, child.unwrap())
        else:
            _unwrap_fake_quant(child)


def distill_loss(model, X, teacher, Xt, i, row, r, tl_s, tl_t, T=2.0):
    """Knowledge distillation on every DJORGE head: soft targets from the teacher at temperature T
    (Hinton et al.; KL scaled by T^2). Spans: soft membership/begin; decisions: soft distributions."""
    t = r["task"]
    if t in ("resolve", "answer"):
        s, q = tl_s[:, :2].float(), tl_t[:, :2].float().to(tl_s.device)
        return F.binary_cross_entropy_with_logits(s / T, torch.sigmoid(q / T)) * T
    if t == "NOUL":
        zs = model.binary_logit(X, i, row).float()
        zt = teacher.binary_logit(Xt, i, row).float().to(zs.device)
        return F.binary_cross_entropy_with_logits(zs / T, torch.sigmoid(zt / T)) * T
    if t == "SCORE":
        ls = model.ordinal_logprobs(X, i, row).float()
        lt = teacher.ordinal_logprobs(Xt, i, row).float().to(ls.device)
        return F.kl_div(ls, lt, log_target=True, reduction="sum")
    zs = model.slot_logits(X, i, row).float()
    zt = teacher.slot_logits(Xt, i, row).float().to(zs.device)
    if t == "SET":
        return F.binary_cross_entropy_with_logits(zs / T, torch.sigmoid(zt / T)) * T
    return F.kl_div(F.log_softmax(zs / T, -1), F.log_softmax(zt / T, -1), log_target=True, reduction="sum") * T * T


def preservation_loss(model, tok, texts, dev, ref=None):
    """|| norm(h_adapted) - norm(h_base) ||^2 on plain text (anti-forgetting).
    ref: frozen copy of the original backbone (stage "full"); otherwise LoRA adapters are switched off."""
    enc = tok(texts, add_special_tokens=False, padding=True, truncation=True, max_length=96, return_tensors="pt")
    ids, mask = enc["input_ids"].to(dev), enc["attention_mask"].to(dev)
    with torch.no_grad():
        if ref is not None:
            rdev = next(ref.parameters()).device
            hb = ref(input_ids=ids.to(rdev), attention_mask=mask.to(rdev), use_cache=False).last_hidden_state.float().to(dev)
        else:
            with lora.disabled(model.backbone):
                hb = model.backbone(input_ids=ids, attention_mask=mask, use_cache=False).last_hidden_state.float()
    ha = model.backbone(input_ids=ids, attention_mask=mask, use_cache=False).last_hidden_state.float()
    m = mask.unsqueeze(-1).float()
    d = (F.layer_norm(ha, ha.shape[-1:]) - F.layer_norm(hb, hb.shape[-1:])) ** 2
    return (d * m).sum() / (m.sum() * ha.shape[-1])


def dev_score(model, tok, max_tokens, dev):
    """Accuracy on the djorge-real DEV split through the full runtime (rules + model + verifier).
    Used only for checkpoint selection; the hidden split is never touched here."""
    import importlib.util
    from .. import Djorge, DjorgeConfig
    from ..runtime.semantic import SemanticEngine
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    spec = importlib.util.spec_from_file_location("real_eval", os.path.join(here, "benchmarks", "real_eval.py"))
    rev = importlib.util.module_from_spec(spec); spec.loader.exec_module(rev)
    items = [i for i in json.load(open(os.path.join(here, "benchmarks", "real", "djorge_real_v1.json"))) if i["split"] == "dev"]
    was = model.training
    model.eval()
    cfg = DjorgeConfig(); cfg.context_dir = "/tmp/djorge-select-ctx"
    eng = SemanticEngine(model, tok, None, {"model_id": "selecting"}, device=dev, max_tokens=max_tokens)
    z = Djorge(config=cfg, semantic=eng)
    ok, hi_err, hi = 0, 0, 0
    for it in items:
        try:
            c, answered, p, _ = rev.score_item(it, z.execute(it["query"]))
        except Exception:
            c, answered, p = False, False, None
        ok += c
        if answered and p is not None and p >= 0.9:
            hi += 1; hi_err += (not c)
    model.train(was)
    return {"accuracy": ok / len(items), "high_conf_errors": hi_err, "high_conf_n": hi}


def _fits(ser, r) -> bool:
    try:
        build_rows(ser, r, random.Random(0))
        return True
    except (ValueError, KeyError, TypeError):
        return False


def val_score(model, tok, max_tokens, dev, recs) -> dict:
    """Held-out validation (synthetic validation split + held-out public records), used together with the
    27-item real dev set so checkpoint selection is not decided by 1-2 noisy dev items."""
    from ..runtime.semantic import SemanticEngine
    from .evaluate import answer_metrics, decision_metrics, span_metrics
    was = model.training
    model.eval()
    eng = SemanticEngine(model, tok, None, {"model_id": "selecting"}, device=dev, max_tokens=max_tokens)
    parts = {}
    res = [r for r in recs if r["task"] == "resolve"]
    ans = [r for r in recs if r["task"] == "answer"]
    dec = [r for r in recs if r["task"] in ("CHOICE", "NOUL", "SCORE", "SET", "RANK")]
    if res:
        parts["span_f1"] = span_metrics(eng, res)["f1"]
    if ans:
        parts["answer_f1"] = answer_metrics(eng, ans)["token_f1"]
    if dec:
        dm = decision_metrics(eng, dec)
        accs = [v.get("accuracy", v.get("exact_match", v.get("top1"))) for v in dm.values()]
        parts["decision_acc"] = float(np.mean([x for x in accs if x is not None]))
    model.train(was)
    parts["score"] = float(np.mean(list(parts.values()))) if parts else 0.0
    return parts


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--backbone", default="tiny")
    ap.add_argument("--out", default="artifacts/djorge-tiny")
    ap.add_argument("--stage", default="lora", choices=["lora", "frozen", "full"],
                    help="full = no LoRA: the top --unfreeze-top Qwen layers are trained directly; the saved artifact "
                         "ships its own (truncated, modified) backbone - one standalone model")
    ap.add_argument("--steps", type=int, default=1500)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--lr", type=float, default=2e-3)
    ap.add_argument("--lora-lr", type=float, default=1e-3)
    ap.add_argument("--lora-r", type=int, default=16)
    ap.add_argument("--d", type=int, default=512)
    ap.add_argument("--heads", type=int, default=8)
    ap.add_argument("--preserve", type=float, default=0.1)
    ap.add_argument("--max-tokens", type=int, default=192)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--device", default="auto")
    ap.add_argument("--save-every", type=int, default=250)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--data-scale", type=float, default=1.0)
    ap.add_argument("--amp", default="none", choices=["none", "fp16", "bf16"],
                    help="mixed precision for the forward (fp32 master weights). fp16 + loss scaling: 2-3x on T4/P100")
    ap.add_argument("--dtype", default="auto", help="auto = bf16 if supported else float32 (fp16 training is avoided)")
    ap.add_argument("--public", action="store_true", help="mix in public datasets (needs `datasets` + hub access)")
    ap.add_argument("--keep-layers", type=int, default=None, help="truncate the Qwen stack to the first N layers")
    ap.add_argument("--lora-targets", default="q_proj,k_proj,v_proj,o_proj,gate_proj,up_proj,down_proj")
    ap.add_argument("--rslora", action="store_true", help="rank-stabilized LoRA scaling alpha/sqrt(r)")
    ap.add_argument("--loraplus-ratio", type=float, default=1.0, help="LoRA+: lr(B) = ratio * lr(A); 16 is the paper default")
    ap.add_argument("--lora-alpha", type=float, default=None, help="default 2*r")
    ap.add_argument("--public-file", default=None, help="JSONL produced by djorge.training.public_data (mixed into train)")
    ap.add_argument("--select-on-dev", action="store_true", help="evaluate djorge-real dev every --eval-every steps; keep best")
    ap.add_argument("--eval-every", type=int, default=500)
    ap.add_argument("--select-val", type=int, default=0,
                    help="also score N held-out validation records (synthetic + public) when selecting; "
                         "selection key = 0.5*dev + 0.5*val (recommended 600)")
    ap.add_argument("--public-weight", type=float, default=None,
                    help="fraction of each batch drawn from --public-file records (default: natural proportion)")
    ap.add_argument("--quant-aware", action="store_true",
                    help="QAT: round the TRAINED backbone layers to int8 per-channel in the forward pass "
                         "(straight-through), so the deployed int8 model keeps accuracy. Unwrapped before saving.")
    ap.add_argument("--qat-scope", default="all", choices=["all", "trained"],
                    help="all = fake-quant every layer (frozen ones too: trained layers learn to compensate their error)")
    ap.add_argument("--qat-act-levels", type=int, default=127, help="127 = x86 reduce_range, 255 = ARM/qnnpack")
    ap.add_argument("--teacher", default=None, help="artifact used as teacher: knowledge distillation on every record")
    ap.add_argument("--distill-weight", type=float, default=0.5, help="share of the loss from the teacher (0..1)")
    ap.add_argument("--distill-t", type=float, default=2.0, help="distillation temperature")
    ap.add_argument("--no-distill-areas", default="", help="comma list of meta.area learned from labels only "
                    "(e.g. security: a skill the teacher does not have)")
    ap.add_argument("--ema", type=float, default=0.0, help="EMA decay of trainable weights (e.g. 0.999); 0 = off")
    ap.add_argument("--ema-start", type=float, default=0.1, help="fraction of training after which EMA starts")
    ap.add_argument("--llrd", type=float, default=1.0, help="layer-wise LR decay for unfrozen backbone layers (e.g. 0.85)")
    ap.add_argument("--init-from", default=None,
                    help="continue from a trained artifact (decision core + its own backbone if standalone): soft retune")
    ap.add_argument("--unfreeze-top", type=int, default=8, help="stage full: number of top decoder layers trained")
    ap.add_argument("--bb-lr", type=float, default=2e-5, help="stage full: learning rate of the unfrozen backbone layers")
    ap.add_argument("--merge", action="store_true", help="also export a merged standalone derivative checkpoint")
    ap.add_argument("--accum", type=int, default=1, help="gradient accumulation steps (long windows with small batches)")
    ap.add_argument("--grad-ckpt", action="store_true", help="gradient checkpointing in the backbone (long windows)")
    ap.add_argument("--soft-weight", type=float, default=0.5, help="share of soft (large-teacher) targets when present")
    ap.add_argument("--val-only", action="store_true", help="checkpoint selection on --select-val records only (no Origo dev set)")
    ap.add_argument("--no-synthetic", action="store_true", help="train only on --public-file records (no Origo generator)")
    ap.add_argument("--teacher-device", default="auto", help="auto = second GPU when present (teachers, anchor, ref)")
    ap.add_argument("--anchor", default=None, help="the model being replaced (Flash atual): focal distillation keeps "
                    "what it already gets right (Positive-Congruent Training)")
    ap.add_argument("--anchor-weight", type=float, default=0.5, help="weight of the focal distillation term")
    ap.add_argument("--anchor-alpha", type=float, default=0.2, help="distillation weight where the anchor is WRONG")
    ap.add_argument("--anchor-tau", type=float, default=0.35, help="anchor loss below this = anchor got it right")
    ap.add_argument("--loss-norm", action="store_true", help="normalise each group's loss by its running mean")
    ap.add_argument("--balance-file", default=None, help="validacao.jsonl: rebalance groups vs the anchor while training")
    ap.add_argument("--balance-every", type=int, default=150)
    ap.add_argument("--balance-n", type=int, default=48, help="validation records per group for balancing")
    ap.add_argument("--balance-k", type=float, default=2.0)
    ap.add_argument("--select-file", default=None, help="escolha.jsonl: checkpoint = fewest groups below the anchor, "
                    "then best mean (replaces the 27-item dev set)")
    ap.add_argument("--select-n", type=int, default=120, help="records per group used for selection")
    a = ap.parse_args(argv)
    if a.select_file:
        a.select_on_dev, a.val_only = True, True
    if a.val_only or a.select_val:            # validation-based selection needs the evaluation loop
        a.select_on_dev = True
    init_meta, cut_to = None, None
    if a.init_from:
        cut_to = a.keep_layers                             # optional: start from a SHALLOWER copy of the parent (depth pruning)
        init_meta = json.load(open(os.path.join(a.init_from, "metadata", "djorge.json")))
        own = os.path.join(a.init_from, init_meta.get("backbone", ""))
        if init_meta.get("backbone_kind") == "tiny":
            a.backbone, a.keep_layers = "tiny", init_meta.get("tiny_keep_layers") or init_meta.get("keep_layers")
        elif init_meta.get("standalone") and os.path.isdir(own):
            a.backbone, a.keep_layers = own, None          # its own (already truncated, modified) backbone
        else:
            a.backbone, a.keep_layers = init_meta["backbone"], init_meta.get("keep_layers")
        mc = init_meta["model_config"]
        a.d, a.heads = mc["d"], mc["heads"]
        parent_layers = mc.get("layers")
        if cut_to and parent_layers and cut_to < parent_layers:
            a.keep_layers = cut_to
    random.seed(a.seed); np.random.seed(a.seed); torch.manual_seed(a.seed)
    os.makedirs(a.out, exist_ok=True)
    dev = pick_device(a.device)
    s = a.data_scale
    recs = [] if a.no_synthetic else generate(int(7000 * s), int(1500 * s), int(5000 * s), int(3000 * s), 800, seed=a.seed)
    if a.public_file:
        bad = 0
        for i, line in enumerate(open(a.public_file, encoding="utf-8").read().split("\n")):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
                assert isinstance(r, dict) and "task" in r and "source" in r
            except (ValueError, AssertionError):
                bad += 1
                continue
            r.setdefault("id", f"pubf{i}")
            r.setdefault("split", "train")
            r["_public"] = True
            recs.append(r)
        if bad:
            print(f"public file: skipped {bad} malformed lines", flush=True)
    if a.public:
        from .data import load_public
        pub = load_public()
        for i, r in enumerate(pub):
            r["id"] = f"pub{i}"
        recs += pub
    if a.select_val:                                   # deterministic 4% public hold-out for selection
        import hashlib
        for r in recs:
            if r.get("_public") and int(hashlib.sha1(r["source"].encode()).hexdigest(), 16) % 25 == 0:
                r["split"] = "validation"
    dver = dataset_version(recs)
    with open(os.path.join(a.out, "dataset.jsonl"), "w") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    meta = {"djorge_version": DJORGE_VERSION, "dataset_version": dver, "seed": a.seed, "train_args": vars(a),
            "max_tokens": a.max_tokens}
    if a.backbone == "tiny":
        corpus = [r["source"] for r in recs] + [r.get("target", "") for r in recs] + \
                 [" ".join(r.get("options", [])) + " " + r.get("ask", "") for r in recs]
        tok = local_bpe_tokenizer(corpus, vocab=4096, path=os.path.join(a.out, "tokenizer"))
        tiny_cfg = dict(hidden=128, layers=4, heads=4, kv_heads=2, vocab=4096, seed=a.seed)
        bb = tiny_qwen3(**tiny_cfg)
        if a.keep_layers:
            from ..model.backbone import truncate_layers
            truncate_layers(bb, a.keep_layers)
            meta_keep = a.keep_layers
        meta.update(backbone_kind="tiny", tiny_config=tiny_cfg, backbone="tiny-qwen3(random-init)", keep_layers=a.keep_layers)
        d, heads = min(a.d, 128), min(a.heads, 4)
    else:
        dt = a.dtype
        if dt == "auto" and a.stage == "full":
            dt = "float32"                     # fp32 master weights: small updates vanish in bf16
            if dev.startswith("cuda"):
                torch.backends.cuda.matmul.allow_tf32 = True; torch.backends.cudnn.allow_tf32 = True
        if dt == "auto":
            dt = "bfloat16" if (dev.startswith("cuda") and torch.cuda.is_bf16_supported()) else "float32"
        bb, tok, info = load_backbone(a.backbone, device=dev, dtype=dt, keep_layers=a.keep_layers)
        tok.save_pretrained(os.path.join(a.out, "tokenizer"))
        meta.update(backbone_kind="hf", backbone=a.backbone, revision=info.get("revision"), backbone_info=info,
                    keep_layers=a.keep_layers)
        d, heads = a.d, a.heads
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    model = DjorgeModel(bb, d=d, heads=heads, lora_r=a.lora_r, lora_alpha=a.lora_alpha or 2 * a.lora_r,
                        use_lora=(a.stage == "lora"), lora_targets=a.lora_targets.split(","), rslora=a.rslora).to(dev)
    ref, bb_train = None, []
    if a.stage == "full":
        import copy
        ref = copy.deepcopy(model.backbone).eval()            # frozen original for the preservation loss
        for p in ref.parameters():
            p.requires_grad = False
        L = len(model.backbone.layers)
        for i in range(max(0, L - a.unfreeze_top), L):
            for p in model.backbone.layers[i].parameters():
                p.requires_grad = True
        if getattr(model.backbone, "norm", None) is not None:
            for p in model.backbone.norm.parameters():
                p.requires_grad = True
        bb_train = [n for n, p in model.named_parameters() if p.requires_grad and n.startswith("backbone.")]
        meta["unfrozen_layers"] = list(range(max(0, L - a.unfreeze_top), L))
    if a.grad_ckpt and hasattr(model.backbone, "gradient_checkpointing_enable"):
        model.backbone.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        model.backbone.config.use_cache = False
        print("gradient checkpointing on", flush=True)
    keep_key = set(bb_train)
    def _keep(k):
        return not k.startswith("backbone.") or ".A." in k or ".B." in k or k in keep_key
    if a.init_from:
        from safetensors.torch import load_file
        cur = model.state_dict()
        core_sd = load_file(os.path.join(a.init_from, "decision", "core.safetensors"))
        skipped = [k for k, v in core_sd.items() if k in cur and cur[k].shape != v.shape]
        model.load_state_dict({k: v.to(dev) for k, v in core_sd.items() if k not in skipped}, strict=False)
        if skipped:
            print("init: re-initialized (shape changed by depth cut):", skipped, flush=True)
        tiny_own = os.path.join(a.init_from, "backbone", "model.safetensors")
        if init_meta.get("backbone_kind") == "tiny" and os.path.exists(tiny_own):
            want = model.backbone.state_dict()
            model.backbone.load_state_dict({k: v.float().to(dev) for k, v in load_file(tiny_own).items() if k in want}, strict=True)
        meta["init_from"] = a.init_from
        meta["backbone_origin"] = init_meta.get("backbone_source", init_meta.get("backbone"))
        print(f"initialized from {a.init_from}", flush=True)
    if a.quant_aware and a.stage == "full":
        _FakeQuantLinear.act_levels = a.qat_act_levels
        first = 0 if a.qat_scope == "all" else max(0, len(model.backbone.layers) - a.unfreeze_top)
        for i in range(first, len(model.backbone.layers)):
            _wrap_fake_quant(model.backbone.layers[i])
        bb_train = [n for n, pm in model.named_parameters() if pm.requires_grad and n.startswith("backbone.")]
        keep_key.clear(); keep_key.update(bb_train)
        meta["quant_aware"] = {"weights": "int8 per-channel symmetric", "activations": f"dynamic per-tensor, {a.qat_act_levels} levels",
                               "scope": a.qat_scope}
        print(f"QAT: weights+activations int8 simulated on layers {first}..{len(model.backbone.layers) - 1}", flush=True)
    ser = Serializer(tok, a.max_tokens)
    core = [p for n, p in model.named_parameters() if p.requires_grad and not n.startswith("backbone.")]
    groups = [{"params": core, "lr": a.lr}]
    if a.stage == "lora":
        pa, pb = lora.adapter_param_groups(model.backbone, "djorge")
        groups.append({"params": pa, "lr": a.lora_lr})
        groups.append({"params": pb, "lr": a.lora_lr * a.loraplus_ratio})       # LoRA+
    if bb_train:
        named = dict(model.named_parameters())
        by_layer = {}
        for n in bb_train:
            m_ = re.search(r"\.layers\.(\d+)\.", n)
            by_layer.setdefault(int(m_.group(1)) if m_ else 10_000, []).append(named[n])
        top = max(k for k in by_layer if k < 10_000) if any(k < 10_000 for k in by_layer) else 0
        for li, ps in sorted(by_layer.items()):
            depth = 0 if li >= 10_000 else top - li                 # top layer (and final norm) get the full LR
            groups.append({"params": ps, "lr": a.bb_lr * (a.llrd ** depth)})
    teacher = None
    no_kd = {x.strip() for x in a.no_distill_areas.split(",") if x.strip()}
    tdev = a.teacher_device
    if tdev == "auto":
        tdev = "cuda:1" if dev.startswith("cuda") and torch.cuda.device_count() > 1 else dev
    if ref is not None and tdev != dev:
        ref = ref.to(tdev)
        torch.cuda.empty_cache()
    print(f"student on {dev}, teachers on {tdev}", flush=True)
    anchor = None
    if a.anchor:
        from ..runtime.semantic import SemanticEngine
        anchor = SemanticEngine.from_artifact(a.anchor, device=tdev,
                                              dtype="float16" if a.amp == "fp16" else "auto").model.eval()
        for p_ in anchor.parameters():
            p_.requires_grad = False
        meta["anchor"] = {"artifact": a.anchor, "weight": a.anchor_weight, "alpha": a.anchor_alpha, "tau": a.anchor_tau}
        print(f"focal distillation from anchor {a.anchor}", flush=True)
    if a.teacher:
        from ..runtime.semantic import SemanticEngine
        teacher = SemanticEngine.from_artifact(a.teacher, device=tdev,
                                               dtype="float16" if a.amp == "fp16" else "auto").model.eval()
        for p_ in teacher.parameters():
            p_.requires_grad = False
        meta["teacher"] = a.teacher
        meta["distill"] = {"weight": a.distill_weight, "T": a.distill_t}
        print(f"distilling from {a.teacher} (weight {a.distill_weight}, T {a.distill_t})", flush=True)
    opt = torch.optim.AdamW(groups, weight_decay=0.01)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=[g["lr"] for g in groups], total_steps=a.steps, pct_start=0.06)
    train = [r for r in recs if r["split"] == "train"]
    if a.public_file or a.public:                      # drop records whose query/options cannot fit the window
        fit, frng = [], random.Random(0)
        for r in train:
            try:
                build_rows(ser, r, frng); fit.append(r)
            except (ValueError, KeyError, TypeError):
                pass
        if len(fit) < len(train):
            print(f"window filter: dropped {len(train) - len(fit)} records that do not fit {a.max_tokens} tokens", flush=True)
        train = fit
    train_pub = [r for r in train if r.get("_public")]
    train_syn = [r for r in train if not r.get("_public")]
    val_recs = []
    if a.select_val:
        vr = random.Random(1234)
        vs = [r for r in recs if r["split"] == "validation" and not r.get("_public")]
        vp = [r for r in recs if r["split"] == "validation" and r.get("_public")]
        k = a.select_val // (2 if vp else 1)
        val_recs = vr.sample(vs, min(k, len(vs))) + vr.sample(vp, min(a.select_val - k, len(vp)))
        val_recs = [r for r in val_recs if _fits(ser, r) and r.get("label") is not None and r.get("known") is None]
        print(f"selection: {'' if a.val_only else 'real dev + '}{len(val_recs)} held-out validation records", flush=True)
    plain = [r["source"] for r in train]
    from .selfreg import group_of as _gof, load_groups, group_scores, group_losses, balance_weights
    by_group = {}
    for r in train:
        by_group.setdefault(_gof(r), []).append(r)
    gnames = sorted(by_group)
    base_share = {g: len(by_group[g]) / len(train) for g in gnames}
    gweights = dict(base_share)
    gema = {}
    bal_groups, anchor_bal, sel_groups, anchor_sel = None, {}, None, {}
    fits = lambda r: _fits(ser, r)
    if a.balance_file and anchor is not None:
        bal_groups = {g: v for g, v in load_groups(a.balance_file, a.balance_n, fits, seed=3).items() if g in by_group}
        anchor_bal = group_losses(anchor, ser, bal_groups, tdev, build_rows, record_loss, a.soft_weight)
        print("balance: anchor loss per group", {g: round(v, 3) for g, v in anchor_bal.items()}, flush=True)
    if a.select_file:
        from ..runtime.semantic import SemanticEngine
        sel_groups = load_groups(a.select_file, a.select_n, fits, seed=5)
        if anchor is not None:
            anchor_sel = group_scores(SemanticEngine(anchor, tok, None, {"model_id": "anchor"}, device=tdev,
                                                     max_tokens=a.max_tokens), sel_groups)
            print("selection: anchor per group", {g: round(v, 3) for g, v in anchor_sel.items()}, flush=True)
        meta["selection_groups"] = {g: len(v) for g, v in sel_groups.items()}
    rng = random.Random(a.seed)
    step0 = 0
    ck = os.path.join(a.out, "checkpoint.pt")
    if a.resume and os.path.exists(ck):
        st = torch.load(ck, map_location=dev, weights_only=False)
        try:                                   # a checkpoint from another configuration cannot be resumed
            opt.load_state_dict(st["opt"]); sched.load_state_dict(st["sched"])
            model.load_state_dict(st["model"], strict=False)
            step0 = st["step"]; rng.setstate(st["rng"])
            print(f"resumed at step {step0}")
        except (ValueError, KeyError, RuntimeError) as e:
            os.replace(ck, ck + ".incompativel")
            print(f"WARNING checkpoint from a different run ignored ({type(e).__name__}); "
                  f"moved to {os.path.basename(ck)}.incompativel, starting from step 0", flush=True)
    n_tr = sum(p.numel() for g in groups for p in g["params"])
    n_all = sum(p.numel() for p in model.parameters())
    print(f"backbone={meta['backbone']} stage={a.stage} trainable={n_tr:,} total={n_all:,} device={dev} data={dver}")
    train_params = [p_ for g in groups for p_ in g["params"]]
    ema_w = None
    if a.resume and os.path.exists(ck):
        _st = torch.load(ck, map_location=dev, weights_only=False)
        if _st.get("ema_w") is not None:
            ema_w = [w.to(dev) for w in _st["ema_w"]]
        del _st

    def _swap_ema():
        if ema_w is None:
            return None
        backup = [p_.detach().clone() for p_ in train_params]
        with torch.no_grad():
            for p_, w in zip(train_params, ema_w):
                p_.copy_(w)
        return backup

    def _restore(backup):
        if backup is not None:
            with torch.no_grad():
                for p_, w in zip(train_params, backup):
                    p_.copy_(w)
    model.train()
    import contextlib
    use_amp = a.amp != "none" and dev.startswith("cuda")
    amp_dt = torch.float16 if a.amp == "fp16" else torch.bfloat16
    amp_ctx = (lambda: torch.autocast("cuda", dtype=amp_dt)) if use_amp else contextlib.nullcontext
    scaler = torch.amp.GradScaler("cuda", enabled=use_amp and a.amp == "fp16")
    if use_amp:
        print(f"mixed precision: {a.amp} (fp32 master weights{', loss scaling' if a.amp == 'fp16' else ''})", flush=True)
    log, t0, ema = [], time.time(), None
    best, best_key, dev_curve = None, None, []
    loss, lp = torch.tensor(0.0), torch.tensor(0.0)
    for step in range(step0 + 1, a.steps + 1):
      opt.zero_grad(set_to_none=True)
      for _micro in range(max(1, a.accum)):
        if a.public_weight is not None and train_pub and train_syn:
            n_pub = sum(rng.random() < a.public_weight for _ in range(a.batch))
            batch = rng.sample(train_pub, n_pub) + rng.sample(train_syn, a.batch - n_pub)
        elif bal_groups is not None or a.loss_norm:
            gs = rng.choices(gnames, weights=[gweights[g] for g in gnames], k=a.batch)
            batch = [rng.choice(by_group[g]) for g in gs]
        else:
            batch = rng.sample(train, a.batch)
        built = [build_rows(ser, r, rng) for r in batch]
        rows = [b[0][0] for b in built]
        with amp_ctx():
            ids, roles, mask = model.collate(rows, dev)
            X = model.encode(ids, roles, mask)
            tls = model.token_logits(X, rows)
            per = [(i, record_loss(model, X, i, rows[i], r, built[i][1], tls[i], dev, a.soft_weight)) for i, r in enumerate(batch)]
            per = [(i, x) for i, x in per if x is not None]
            if not per:
                continue
            if a.loss_norm:
                normed = []
                for i, x in per:
                    g = _gof(batch[i]); v = float(x.detach())
                    if np.isfinite(v):
                        gema[g] = v if g not in gema else 0.98 * gema[g] + 0.02 * v
                    mean_all = float(np.mean(list(gema.values()))) if gema else 1.0
                    normed.append((i, x * (mean_all / max(gema.get(g, mean_all), 1e-3))))
                per = normed
            if anchor is not None:
                with torch.no_grad():
                    Xa = anchor.encode(ids.to(tdev), roles.to(tdev), mask.to(tdev))
                    tla = anchor.token_logits(Xa, rows)
                focal = []
                for i, x in per:
                    with torch.no_grad():
                        ha = record_loss(anchor, Xa, i, rows[i], batch[i], built[i][1], tla[i], tdev, a.soft_weight)
                    right = ha is not None and float(ha) < a.anchor_tau
                    w_i = 1.0 if right else a.anchor_alpha
                    fd = distill_loss(model, X, anchor, Xa, i, rows[i], batch[i], tls[i], tla[i], a.distill_t)
                    focal.append((i, x + a.anchor_weight * w_i * fd))
                per = focal
            if teacher is not None:
                with torch.no_grad():
                    Xt = teacher.encode(ids.to(tdev), roles.to(tdev), mask.to(tdev))
                    tlt = teacher.token_logits(Xt, rows)
                mixed = []
                for i, x in per:
                    r = batch[i]
                    if (r.get("meta") or {}).get("area") in no_kd:       # skill the teacher never learned: labels only
                        mixed.append(x)
                    else:
                        kd = distill_loss(model, X, teacher, Xt, i, rows[i], r, tls[i], tlt[i], a.distill_t)
                        mixed.append((1 - a.distill_weight) * x + a.distill_weight * kd)
                loss = torch.stack(mixed).mean()
            else:
                loss = torch.stack([x for _, x in per]).mean()
            lp = torch.tensor(0.0)
            if a.stage in ("lora", "full") and a.preserve > 0:
                lp = preservation_loss(model, tok, rng.sample(plain, 8), dev, ref=ref)
                loss = loss + a.preserve * lp
        scaler.scale(loss / max(1, a.accum)).backward()
      scaler.unscale_(opt)
      torch.nn.utils.clip_grad_norm_([p for g in groups for p in g["params"]], 1.0)
      scaler.step(opt); scaler.update(); sched.step()
      if not torch.isfinite(loss):                   # fp16 overflow: the scaler skipped this step
          loss = torch.tensor(ema if ema is not None else 0.0)
      if True:
        if a.ema > 0 and step >= int(a.ema_start * a.steps):          # weight averaging (EMA) of trainable weights
            if ema_w is None:
                ema_w = [p_.detach().clone() for p_ in train_params]
            else:
                torch._foreach_mul_(ema_w, a.ema)
                torch._foreach_add_(ema_w, [p_.detach() for p_ in train_params], alpha=1 - a.ema)
        ema = loss.item() if ema is None else 0.95 * ema + 0.05 * loss.item()
        if step % 50 == 0 or step == 1:
            print(f"step {step:5d} loss {ema:.4f} preserve {(lp.item() if a.stage in ('lora', 'full') else float('nan')):.5f} {time.time() - t0:.0f}s", flush=True)
            log.append({"step": step, "loss": round(ema, 5), "preserve": round(lp.item(), 6)})
        if bal_groups and step % a.balance_every == 0:
            stud = group_losses(model, ser, bal_groups, dev, build_rows, record_loss, a.soft_weight)
            gweights = balance_weights(base_share, stud, anchor_bal, k=a.balance_k)
            for g in gnames:
                gweights.setdefault(g, base_share[g])
            top = sorted(((stud.get(g, 0) - anchor_bal.get(g, 0), g) for g in bal_groups), reverse=True)[:3]
            print(f"  balance@{step}: atrás do âncora " + ", ".join(f"{g} {d:+.3f}" for d, g in top), flush=True)
            model.train()
        if a.select_on_dev and (step % a.eval_every == 0 or step == a.steps):
            _bk = _swap_ema()                                # evaluate (and keep) the averaged weights
            ds = dev_score(model, tok, a.max_tokens, dev) if not a.val_only else {"accuracy": 0.0, "high_conf_errors": 0, "high_conf_n": 0}
            ds["step"] = step
            if sel_groups:
                from ..runtime.semantic import SemanticEngine
                was_tr = model.training; model.eval()
                sc = group_scores(SemanticEngine(model, tok, None, {"model_id": "selecting"}, device=dev,
                                                 max_tokens=a.max_tokens), sel_groups)
                model.train(was_tr)
                drops = [g for g in sc if g in anchor_sel and sc[g] < anchor_sel[g] - 0.01]
                ds["groups"] = {g: round(v, 4) for g, v in sc.items()}
                ds["below_anchor"] = drops
                ds["val"] = {"score": float(np.mean(list(sc.values()))) - 0.02 * len(drops)}
                ds["high_conf_errors"] = len(drops)
            if val_recs:
                ds["val"] = val_score(model, tok, a.max_tokens, dev, val_recs)
            ds["select"] = (round(ds["val"]["score"], 4) if a.val_only else round(0.5 * ds["accuracy"] + 0.5 * ds["val"]["score"], 4)) \
                if (val_recs or sel_groups) else ds["accuracy"]
            dev_curve.append(ds)
            key = (ds["select"], -ds["high_conf_errors"])
            if sel_groups:
                print(f"  escolha@{step}: média {ds['val']['score'] + 0.02 * len(ds['below_anchor']):.4f} · abaixo do âncora: "
                      f"{ds['below_anchor'] or '—'} · select {ds['select']}", flush=True)
            else:
                print(f"  dev@{step}: acc {ds['accuracy']:.3f}  high-conf errors {ds['high_conf_errors']}/{ds['high_conf_n']}"
                      + (f"  val {ds['val']}  select {ds['select']}" if val_recs else ""), flush=True)
            if best_key is None or key > best_key:
                best_key = key
                best = {k: v.detach().cpu().clone() for k, v in model.state_dict().items() if _keep(k)}
            _restore(_bk)
        if step % a.save_every == 0 or step == a.steps:
            torch.save({"model": {k: v for k, v in model.state_dict().items() if _keep(k)},
                        "opt": opt.state_dict(), "sched": sched.state_dict(), "step": step, "rng": rng.getstate(),
                        "ema_w": [w.cpu() for w in ema_w] if ema_w is not None else None}, _buf := io.BytesIO())
            with open(ck + ".tmp", "wb") as fh:      # Drive FUSE can't take torch's zip writer directly
                fh.write(_buf.getbuffer())
            os.replace(ck + ".tmp", ck)
            del _buf
    model.eval()
    if best is None and ema_w is not None:
        _swap_ema()
    if best is not None:
        model.load_state_dict(best, strict=False)
        meta["selected_on_dev"] = {"best": max(dev_curve, key=lambda d: (d["select"], -d["high_conf_errors"])), "curve": dev_curve}
        print("restored best dev checkpoint:", meta["selected_on_dev"]["best"])
    meta["train_log"] = log
    meta["train_seconds"] = round(time.time() - t0, 1)
    meta["versions"] = {"djorge": DJORGE_VERSION, "backbone": meta["backbone"], "adapter": f"djorge-lora-{dver[-12:]}",
                        "decision_core": f"core-{dver[-12:]}", "dataset": dver}
    meta["model_id"] = f"djorge-{os.path.basename(a.out.rstrip('/'))}"
    if a.quant_aware and a.stage == "full":
        _unwrap_fake_quant(model.backbone)                 # back to a plain Qwen3Model for a clean export
    if a.stage == "full":                              # standalone model: ship the modified, truncated backbone
        bdir = os.path.join(a.out, "backbone")
        model.backbone.save_pretrained(bdir, safe_serialization=True,
                                       state_dict={k: v.detach().to(torch.bfloat16).contiguous()
                                                   for k, v in model.backbone.state_dict().items()})
        tok.save_pretrained(bdir)
        if meta.get("backbone_kind") == "tiny":
            meta.update(standalone=True, tiny_keep_layers=meta.get("keep_layers"), keep_layers=None)
        else:
            meta.update(backbone_source=meta["backbone"], backbone="backbone", keep_layers=None, revision=None,
                        standalone=True)
        meta["versions"]["backbone"] = f"djorge-own-{dver[-12:]}"
        del ref
    model.save(a.out, meta, merged=a.merge)
    print("saved", a.out)
    return a.out


if __name__ == "__main__":
    main()
