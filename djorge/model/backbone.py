"""Qwen backbone access.

Production: any Qwen text checkpoint loadable by transformers (default
`Qwen/Qwen3-0.6B`; candidates benchmarked: Qwen3-1.7B, Qwen3-Embedding-0.6B,
Qwen3.5-0.8B). We load the *base* decoder (`AutoModel`, no LM head): DJORGE never
calls the LM head or generate(); it consumes hidden states.

Tests/CI: `tiny_qwen3()` builds a randomly initialized model with the real
`Qwen3Model` class and config schema, so every code path is the production one.
"""
from __future__ import annotations

from typing import Tuple

import os
import torch
import torch.nn as nn

DEFAULT_BACKBONE = "Qwen/Qwen3-0.6B"


def pick_device(device: str = "auto") -> str:
    if device != "auto":
        return device
    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def pick_dtype(dtype: str, device: str):
    if dtype != "auto":
        return getattr(torch, dtype)
    if device.startswith("cuda"):
        return torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    return torch.float32


def _text_model(m: nn.Module) -> nn.Module:
    """Return the text decoder stack (handles multimodal wrappers such as Qwen3.5)."""
    for attr in ("language_model", "text_model"):
        if hasattr(m, attr):
            return getattr(m, attr)
    if hasattr(m, "model") and hasattr(m.model, "language_model"):
        return m.model.language_model
    return m


def truncate_layers(model: nn.Module, keep: int) -> nn.Module:
    """Keep only the first `keep` decoder layers (drop the top of the stack). Origo reads hidden states
    from a learned mix of layers, so upper layers that the mix ignores are pure cost."""
    layers = model.layers
    if keep is None or keep >= len(layers):
        return model
    model.layers = nn.ModuleList(list(layers)[:keep])
    model.config.num_hidden_layers = keep
    if hasattr(model.config, "layer_types") and model.config.layer_types:
        model.config.layer_types = model.config.layer_types[:keep]
    return model


def quiet_transformers():
    """The 'lm_head.weight | UNEXPECTED' load report is EXPECTED: DJORGE loads Qwen3Model without the LM head
    (no text generation). Silence it and the per-tensor progress bars."""
    try:
        from transformers.utils import logging as hf_logging
        hf_logging.set_verbosity_error()
        hf_logging.disable_progress_bar()
    except Exception:
        pass
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")


def load_backbone(name_or_path: str = DEFAULT_BACKBONE, device: str = "auto", dtype: str = "auto",
                  revision: str = None, keep_layers: int = None, quant: str = None) -> Tuple[nn.Module, object, dict]:
    """quant: None | "int8" | "nf4" (bitsandbytes, CUDA only; weight-only)."""
    from transformers import AutoModel, AutoTokenizer
    quiet_transformers()
    device = pick_device(device)
    dt = pick_dtype(dtype, device)
    tok = AutoTokenizer.from_pretrained(name_or_path, revision=revision)
    kw = {}
    if quant in ("int8", "nf4"):
        from transformers import BitsAndBytesConfig
        kw["quantization_config"] = (BitsAndBytesConfig(load_in_8bit=True) if quant == "int8" else
                                     BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                                        bnb_4bit_compute_dtype=torch.bfloat16 if dt == torch.bfloat16 else torch.float16,
                                                        bnb_4bit_use_double_quant=True))
        kw["device_map"] = {"": device}
    model = AutoModel.from_pretrained(name_or_path, revision=revision, dtype=dt, **kw)
    model = _text_model(model)
    if not quant:
        model = model.to(device)
    model = truncate_layers(model, keep_layers)
    cfg = model.config
    info = {"backbone": name_or_path, "revision": revision, "hidden_size": cfg.hidden_size,
            "num_layers": cfg.num_hidden_layers, "dtype": str(dt), "device": device, "quant": quant,
            "keep_layers": keep_layers}
    return model, tok, info


def tiny_qwen3(hidden: int = 128, layers: int = 4, heads: int = 4, kv_heads: int = 2, vocab: int = 4096,
               seed: int = 0) -> nn.Module:
    from transformers import Qwen3Config, Qwen3Model
    torch.manual_seed(seed)
    cfg = Qwen3Config(vocab_size=vocab, hidden_size=hidden, intermediate_size=hidden * 3,
                      num_hidden_layers=layers, num_attention_heads=heads, num_key_value_heads=kv_heads,
                      head_dim=hidden // heads, max_position_embeddings=2048, tie_word_embeddings=True)
    return Qwen3Model(cfg)


def local_bpe_tokenizer(corpus, vocab: int = 4096, path: str = None):
    """Byte-level BPE trained locally - a stand-in for the Qwen tokenizer when the
    hub is unreachable (same interface: offsets mapping, fast tokenizer)."""
    from tokenizers import Tokenizer, models, pre_tokenizers, decoders, trainers
    from transformers import PreTrainedTokenizerFast
    t = Tokenizer(models.BPE())
    t.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    t.decoder = decoders.ByteLevel()
    tr = trainers.BpeTrainer(vocab_size=vocab, special_tokens=["<|pad|>"],
                             initial_alphabet=pre_tokenizers.ByteLevel.alphabet())
    t.train_from_iterator(corpus, tr)
    fast = PreTrainedTokenizerFast(tokenizer_object=t, pad_token="<|pad|>")
    if path:
        fast.save_pretrained(path)
    return fast
