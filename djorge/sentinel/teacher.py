"""Distillation from a larger Qwen: write soft targets (`soft`) into the Sentinel training records.

The teacher (e.g. Qwen/Qwen3-14B on an A100, Qwen/Qwen3-8B on an L4) reads each CHOICE / SCORE record and we take its
next-token probabilities over the option numbers "1".."K" (no text generation). The trainer then learns from
both the gold label and the teacher distribution (--soft-weight).
Records where the teacher puts < 5% on the gold label get weight x0.5 (possible label noise) and
meta.teacher_disagrees = true, so they can be reviewed.

    python -m djorge.sentinel.teacher --teacher Qwen/Qwen3-14B --data train.jsonl --out train_soft.jsonl [--max 30000]
"""
from __future__ import annotations

import argparse
import json
import random

import torch

SYSTEM = "Você é um classificador cuidadoso. Responda apenas com o número da opção correta."


def _prompt(tok, r):
    descs = r.get("descriptions") or [""] * len(r["options"])
    opts = "\n".join(f"{i + 1}. {o}" + (f" — {d}" if d else "") for i, (o, d) in enumerate(zip(r["options"], descs)))
    user = f"Texto:\n<<<\n{r['source'][:3000]}\n>>>\n\nPergunta: {r.get('ask', '')}\nOpções:\n{opts}\n\nNúmero da opção:"
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
    try:
        return tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True, enable_thinking=False)
    except TypeError:
        return tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)


def label_records(model, tok, recs, max_n=30000, batch=8, seed=0):
    digit_ids = [tok.convert_tokens_to_ids(str(i)) for i in range(1, 10)]
    cand = [i for i, r in enumerate(recs) if r.get("task") in ("CHOICE", "SCORE") and r.get("split") == "train"
            and 2 <= len(r.get("options", [])) <= 9]
    random.Random(seed).shuffle(cand)
    cand = cand[:max_n]
    tok.padding_side = "left"
    stats = {"labelled": 0, "agree": 0, "disagree": 0}
    dev = next(model.parameters()).device
    for s in range(0, len(cand), batch):
        idx = cand[s:s + batch]
        enc = tok([_prompt(tok, recs[i]) for i in idx], return_tensors="pt", padding=True).to(dev)
        with torch.no_grad():
            logits = model(**enc).logits[:, -1, :].float()
        for j, i in enumerate(idx):
            r = recs[i]
            k = len(r["options"])
            p = torch.softmax(logits[j, digit_ids[:k]], -1).tolist()
            r["soft"] = [round(x, 5) for x in p]
            stats["labelled"] += 1
            if r.get("label") is not None:
                g = int(r["label"]) if r["task"] == "SCORE" else r["options"].index(r["label"])
                if p[g] < 0.05:
                    r["weight"] = round(float(r.get("weight", 1.0)) * 0.5, 3)
                    r.setdefault("meta", {})["teacher_disagrees"] = True
                    stats["disagree"] += 1
                elif max(range(k), key=lambda t: p[t]) == g:
                    stats["agree"] += 1
    return stats


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--teacher", default="Qwen/Qwen3-14B")
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--max", type=int, default=30000)
    ap.add_argument("--batch", type=int, default=8)
    a = ap.parse_args(argv)
    from transformers import AutoModelForCausalLM, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(a.teacher)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    model = AutoModelForCausalLM.from_pretrained(a.teacher, torch_dtype=torch.bfloat16, device_map="auto").eval()
    recs = [json.loads(l) for l in open(a.data, encoding="utf-8") if l.strip()]
    st = label_records(model, tok, recs, a.max, a.batch)
    with open(a.out, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(json.dumps({"teacher": a.teacher, **st}, ensure_ascii=False))


if __name__ == "__main__":
    main()
