"""Build step: puts the models INSIDE the package (modelos/<nome> next to the executable).

modelo-fonte.txt, one line per model:  <nome>=<fonte>   (ex.: flash=gdrive:1LIF..., sentinel=gdrive:1ceo...)
Sources:
  gdrive:<file id>                      a .zip of the model folder shared "anyone with the link"
  gdrive-folder:<folder id>[:<subpath>] a shared Drive folder (optionally a subfolder of it)
  hf:<owner/repo>                       a Hugging Face repo (HF_TOKEN env for private repos)
If the artifact does not carry its own backbone, the base Qwen backbone is downloaded into it, so the
installed program never needs the internet.
"""
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def find_artifact(root: Path) -> Path:
    for p in [root, *root.rglob("*")]:
        if p.is_dir() and (p / "metadata" / "djorge.json").is_file():
            return p
    sys.exit(f"nenhum modelo DJORGE (metadata/djorge.json) em {root}")


def main():
    lines = [l.strip() for l in (HERE / "modelo-fonte.txt").read_text().splitlines() if l.strip() and not l.startswith("#")]
    for l in lines:
        name, _, src = l.partition("=") if "=" in l.split(":")[0] else ("flash", "", l)
        fetch(name.strip(), src.strip())


def fetch(name, src):
    OUT = HERE / "modelos" / name
    tmp = HERE / f"_modelo_tmp_{name}"
    shutil.rmtree(tmp, ignore_errors=True); tmp.mkdir()
    kind, _, rest = src.partition(":")
    if kind == "gdrive":
        import gdown
        z = tmp / "m.zip"
        gdown.download(id=rest, output=str(z), quiet=False)
        zipfile.ZipFile(z).extractall(tmp / "x"); z.unlink()
    elif kind == "gdrive-folder":
        import gdown
        fid, _, sub = rest.partition(":")
        gdown.download_folder(id=fid, output=str(tmp / "x"), quiet=True)
        if sub:
            cands = [p for p in (tmp / "x").rglob(sub) if p.is_dir()]
            if not cands:
                sys.exit(f"subpasta {sub} não encontrada")
            shutil.move(str(cands[0]), str(tmp / "sel")); shutil.rmtree(tmp / "x"); (tmp / "sel").rename(tmp / "x")
    elif kind == "hf":
        from huggingface_hub import snapshot_download
        snapshot_download(rest, local_dir=str(tmp / "x"), token=os.environ.get("HF_TOKEN") or None)
    else:
        sys.exit("modelo-fonte.txt: use gdrive:, gdrive-folder: ou hf:")
    art = find_artifact(tmp / "x")
    for junk in art.glob("checkpoint.pt*"):
        junk.unlink()
    for junk in ("dataset.jsonl", "playground_origo_v1.json"):
        (art / junk).unlink(missing_ok=True)
    meta = json.load(open(art / "metadata" / "djorge.json"))
    bb = meta.get("backbone")
    if meta.get("backbone_kind") != "tiny" and bb and not (art / bb).is_dir():
        from huggingface_hub import snapshot_download        # offline install: carry the base backbone too
        snapshot_download(bb, revision=meta.get("revision"), local_dir=str(art / "backbone"),
                          allow_patterns=["*.json", "*.safetensors", "*.txt", "*.model", "merges.txt", "vocab.json"])
        meta["backbone"] = "backbone"; meta.pop("revision", None)
        json.dump(meta, open(art / "metadata" / "djorge.json", "w"), indent=1)
    shutil.rmtree(OUT, ignore_errors=True); OUT.parent.mkdir(exist_ok=True)
    shutil.move(str(art), str(OUT)); shutil.rmtree(tmp, ignore_errors=True)
    size = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file()) / 1e9
    print(f"modelo {name} pronto em {OUT} ({size:.2f} GB)")


if __name__ == "__main__":
    main()
