"""ONNX encoder for local use (Origo Flash Local): the whole Qwen + adapter forward as one ONNX graph, run by
ONNX Runtime. The small decision heads stay in PyTorch (they cost microseconds).

Why: on ordinary CPUs ONNX Runtime with int8 weights is faster and uses far less memory than PyTorch fp32, and
the same graph runs on any GPU through DirectML (Windows), CoreML (macOS), CUDA, or the WebGPU provider.

    export_encoder(artifact, out_dir)          -> out_dir/onnx/encoder.onnx (fp32) + encoder.int8.onnx
    load_onnx_engine(artifact, onnx_file, providers)   (used by SemanticEngine.from_artifact when asked)

The export is validated against PyTorch on real rows before use (max |diff| and decision agreement): an export
that changes answers is never shipped.
"""
from __future__ import annotations

import json
import os
from typing import List, Optional

import torch
import torch.nn as nn

PREFERRED = ["WebGpuExecutionProvider", "DmlExecutionProvider", "CoreMLExecutionProvider",
             "CUDAExecutionProvider", "CPUExecutionProvider"]


class _EncoderGraph(nn.Module):
    """Export-friendly copy of DjorgeModel.encode (no boolean indexing)."""

    def __init__(self, m):
        super().__init__()
        self.m = m

    def forward(self, ids, roles, mask):
        m = self.m
        emb = m.backbone.get_input_embeddings()
        base = emb(ids.clamp(min=0))
        n_sp = m.special.shape[0]
        sp_idx = (-ids - 1).clamp(min=0, max=n_sp - 1)
        sp_vec = m.special[sp_idx].to(base.dtype)
        base = torch.where((ids < 0).unsqueeze(-1), sp_vec, base)
        out = m.backbone(inputs_embeds=base, attention_mask=mask.long(), output_hidden_states=True, use_cache=False)
        hs = out.hidden_states
        w = torch.softmax(m.mix, 0)
        h = sum(w[i] * hs[l].float() for i, l in enumerate(m.mix_layers))
        x = m.proj(h) + m.role(roles)
        x = m.adapter(x, src_key_padding_mask=~mask)
        return m.ln(x)


def export_encoder(artifact: str, out_dir: str, opset: int = 17, int8: bool = True, fp16: bool = False) -> dict:
    from .semantic import SemanticEngine
    eng = SemanticEngine.from_artifact(artifact, device="cpu", dtype="float32")
    model = eng.model.eval().float()
    g = _EncoderGraph(model).eval()
    os.makedirs(os.path.join(out_dir, "onnx"), exist_ok=True)
    fp32 = os.path.join(out_dir, "onnx", "encoder.onnx")
    ids = torch.tensor([[11, 12, -1, 13, 14, 15, 16], [21, -2, 22, 23, 0, 0, 0]], dtype=torch.long)  # B=2, T=7:
    roles = torch.tensor([[0, 0, 1, 1, 2, 2, 3], [0, 1, 1, 2, 0, 0, 0]], dtype=torch.long)      # sizes 1 would be
    mask = ids != 0                                                                             # specialised away
    try:
        torch.backends.mha.set_fastpath_enabled(False)
    except Exception:
        pass
    from torch.export import Dim
    B, T = Dim("B", min=1, max=64), Dim("T", min=2, max=4096)
    with torch.no_grad():
        prog = torch.onnx.export(g, (ids, roles, mask), input_names=["ids", "roles", "mask"], output_names=["X"],
                                 dynamic_shapes={"ids": {0: B, 1: T}, "roles": {0: B, 1: T}, "mask": {0: B, 1: T}},
                                 opset_version=max(opset, 18), dynamo=True)
        prog.optimize()
        prog.save(fp32, external_data=True)                 # weights beside the graph (> 2 GB protobuf limit)
    files = {"fp32": "onnx/encoder.onnx"}
    if int8:
        from onnxruntime.quantization import QuantType, quantize_dynamic
        q = os.path.join(out_dir, "onnx", "encoder.int8.onnx")
        quantize_dynamic(fp32, q, weight_type=QuantType.QInt8, per_channel=True, use_external_data_format=False)
        files["int8"] = "onnx/encoder.int8.onnx"
    if fp16:
        import onnx
        from onnxconverter_common import float16
        m16 = float16.convert_float_to_float16(onnx.load(fp32), keep_io_types=True, disable_shape_infer=True)
        onnx.save(m16, os.path.join(out_dir, "onnx", "encoder.fp16.onnx"), save_as_external_data=True,
                  location="encoder.fp16.onnx.data")
        files["fp16"] = "onnx/encoder.fp16.onnx"
    info = {"files": files, "hidden": model.cfg["hidden"], "layers": model.cfg["layers"], "d": model.cfg["d"],
            "opset": max(opset, 18)}
    json.dump(info, open(os.path.join(out_dir, "onnx", "encoder.json"), "w"), indent=1)
    return info


class OnnxEncoder:
    def __init__(self, path: str, providers: Optional[List[str]] = None, threads: int = 0):
        import onnxruntime as ort
        avail = ort.get_available_providers()
        prov = [p for p in (providers or PREFERRED) if p in avail] or ["CPUExecutionProvider"]
        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        if threads:
            so.intra_op_num_threads = threads
        self.sess = ort.InferenceSession(path, so, providers=prov)
        self.provider = self.sess.get_providers()[0]

    def __call__(self, ids, roles, mask):
        out = self.sess.run(["X"], {"ids": ids.cpu().numpy(), "roles": roles.cpu().numpy(),
                                    "mask": mask.cpu().numpy()})[0]
        return torch.from_numpy(out)


class _StubBackbone(nn.Module):
    """Stands in for the Qwen backbone when the ONNX encoder does the forward: only the config is needed."""

    def __init__(self, hidden: int, layers: int):
        super().__init__()
        from types import SimpleNamespace
        self.config = SimpleNamespace(hidden_size=hidden, num_hidden_layers=layers)
        self.emb = nn.Embedding(2, hidden)
        self.layers = nn.ModuleList()

    def get_input_embeddings(self):
        return self.emb


def onnx_file_for(path: str, prefer: str = "int8") -> Optional[str]:
    info_p = os.path.join(path, "onnx", "encoder.json")
    if not os.path.exists(info_p):
        return None
    files = json.load(open(info_p))["files"]
    for k in (prefer, "int8", "fp16", "fp32"):
        if k in files and os.path.exists(os.path.join(path, files[k])):
            return os.path.join(path, files[k])
    return None


def build_model_without_backbone(path: str, meta: dict):
    """DjorgeModel with a stub backbone (no Qwen weights in memory) + its trained decision parts."""
    from ..model.djorge_model import DjorgeModel
    info = json.load(open(os.path.join(path, "onnx", "encoder.json")))
    mc = meta["model_config"]
    model = DjorgeModel(_StubBackbone(info["hidden"], info["layers"]), d=mc["d"], adapter_layers=mc["adapter_layers"],
                        heads=mc["heads"], lora_r=mc["lora_r"], lora_alpha=mc["lora_alpha"], use_lora=False,
                        lora_targets=mc.get("lora_targets"), rslora=mc.get("rslora", False))
    from safetensors.torch import load_file
    sd = {k: v for k, v in load_file(os.path.join(path, "decision", "core.safetensors")).items()
          if not k.startswith("backbone.")}
    model.load_state_dict(sd, strict=False)
    return model.eval().float()
