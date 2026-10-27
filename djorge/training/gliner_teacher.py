"""GLiNER as a TEACHER for Origo's span head (entities in Portuguese public texts).

GLiNER (Zaratiana et al., 2023) extracts any entity type named in natural language, in parallel, with a compact
bidirectional encoder. Here it labels PUBLIC texts; its spans become Origo `resolve` records (mode ENTITY), so the
capability is LEARNED by Origo's own span head - GLiNER is not a production dependency.

    python -m djorge.training.gliner_teacher --out spans_pt.jsonl --hf fake-news-UFG/central_de_fatos \
        --column text_news --max 6000 [--model urchade/gliner_multi-v2.1] [--allow-nc]

Safety of the labels: spans are kept only if text[start:end] matches exactly and the score >= --min-score; texts
are split into ~600-character windows on sentence boundaries; every record carries the teacher id and licences.
The model licence is read from the Hub card; a non-commercial licence stops the run unless --allow-nc is given.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re

LABELS = {   # Origo target (pt) -> GLiNER label prompt
    "Pessoa": "pessoa", "Organização": "organização", "Local": "local", "Data": "data",
    "Valor monetário": "valor em dinheiro", "Quantidade": "quantidade", "Produto": "produto",
    "Documento": "documento", "Evento": "evento",
}
NC = ("cc-by-nc", "nc-sa", "non-commercial", "noncommercial")


def windows(text: str, size: int = 600):
    sents = re.split(r"(?<=[.!?])\s+", text.strip())
    cur, start, pos = "", 0, 0
    for s in sents:
        i = text.find(s, pos)
        if i < 0:
            continue
        if cur and len(cur) + len(s) + 1 > size:
            yield start, cur
            cur, start = "", i
        if not cur:
            start = i
        cur = text[start:i + len(s)]
        pos = i + len(s)
    if cur:
        yield start, cur


def licence_of(model_id: str) -> str:
    try:
        from huggingface_hub import model_info
        info = model_info(model_id)
        card = getattr(info, "card_data", None) or getattr(info, "cardData", None) or {}
        lic = (card.get("license") if isinstance(card, dict) else getattr(card, "license", None)) or ""
        return str(lic)
    except Exception as e:
        return f"unknown ({type(e).__name__})"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--hf", required=True, help="Hugging Face dataset id with Portuguese texts")
    ap.add_argument("--config", default=None)
    ap.add_argument("--split", default="train")
    ap.add_argument("--column", required=True)
    ap.add_argument("--model", default="urchade/gliner_multi-v2.1")
    ap.add_argument("--max", type=int, default=6000, help="max text windows")
    ap.add_argument("--min-score", type=float, default=0.5)
    ap.add_argument("--test-share", type=float, default=0.1)
    ap.add_argument("--allow-nc", action="store_true")
    ap.add_argument("--dataset-license", default="see dataset card")
    a = ap.parse_args(argv)

    lic = licence_of(a.model)
    print(f"teacher {a.model} | licence: {lic}", flush=True)
    if any(x in lic.lower() for x in NC) and not a.allow_nc:
        raise SystemExit("teacher licence is non-commercial: use --allow-nc only for research")
    from datasets import load_dataset
    from gliner import GLiNER
    import torch
    model = GLiNER.from_pretrained(a.model)
    if torch.cuda.is_available():
        model = model.to("cuda")
    ds = load_dataset(a.hf, a.config, split=a.split) if a.config else load_dataset(a.hf, split=a.split)
    if a.column not in ds.column_names:
        raise SystemExit(f"column {a.column!r} not in {ds.column_names}")
    rng = random.Random(0)
    prompts = list(LABELS.values()); back = {v: k for k, v in LABELS.items()}
    n_win = n_rec = n_drop = 0
    with open(a.out, "w", encoding="utf-8") as f:
        for row in ds.shuffle(seed=0):
            text = row.get(a.column)
            if not isinstance(text, str) or len(text) < 40:
                continue
            for off, win in windows(text):
                if n_win >= a.max:
                    break
                n_win += 1
                ents = model.predict_entities(win, prompts, threshold=a.min_score)
                by = {k: [] for k in LABELS}
                for e in ents:
                    s, t = int(e["start"]), int(e["end"])
                    if win[s:t] != e["text"] or e["label"] not in back:
                        n_drop += 1; continue
                    by[back[e["label"]]].append([s, t])
                h = int(hashlib.sha1(win.encode("utf-8", "ignore")).hexdigest(), 16) % 100
                split = "test" if h < a.test_share * 100 else ("calibration" if h < a.test_share * 100 + 5 else "train")
                for target, spans in by.items():
                    if not spans and rng.random() > 0.35:          # keep some negatives (no entity of that type)
                        continue
                    spans = sorted({tuple(x) for x in spans})
                    rec = {"task": "resolve", "split": split, "mode": "ENTITY", "target": target, "source": win,
                           "spans": [list(x) for x in spans], "polarity": ["asserted"] * len(spans),
                           "meta": {"lang": "pt", "provenance": f"gliner_teacher:{a.hf}", "teacher": a.model,
                                    "teacher_license": lic, "license": a.dataset_license, "synthetic_labels": True}}
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n"); n_rec += 1
            if n_win >= a.max:
                break
    print(json.dumps({"windows": n_win, "records": n_rec, "dropped_invalid_spans": n_drop, "teacher": a.model,
                      "teacher_license": lic}, ensure_ascii=False))


if __name__ == "__main__":
    main()
