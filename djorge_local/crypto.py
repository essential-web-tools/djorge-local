"""Encrypted model packages (.djx): AES-256-GCM in 4 MiB chunks, key from a passphrase via scrypt.

Format: b"DJX1" | salt(16) | n_log2(1) | manifest_len(4) | manifest(GCM) | chunks...
  each chunk: len(4) | nonce(12) | ciphertext+tag ; the chunk index and the file id are authenticated (AAD),
  so chunks cannot be reordered, dropped or swapped between files.
The plaintext is a tar of the artifact folder. The manifest stores the SHA-256 of the tar and the model name.

What this protects: the download and the copy on disk (useless without the passphrase).
What it cannot protect: a person who has the passphrase and runs the model can read the weights in memory.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import secrets
import struct
import tarfile
import tempfile
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"DJX1"
CHUNK = 4 * 1024 * 1024
N_LOG2 = 15          # scrypt N = 2^15, r = 8, p = 1 (~32 MiB, ~0.1 s)


class BadKey(Exception):
    pass


def _key(passphrase: str, salt: bytes, n_log2: int = N_LOG2) -> bytes:
    if not passphrase or len(passphrase) < 10:
        raise BadKey("a chave precisa ter pelo menos 10 caracteres")
    return Scrypt(salt=salt, length=32, n=2 ** n_log2, r=8, p=1).derive(passphrase.encode())


def _tar_dir(src: Path) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        for p in sorted(src.rglob("*")):
            if p.is_file() and not p.name.startswith("checkpoint.pt"):
                t.add(p, arcname=str(p.relative_to(src)))
    return buf.getvalue()


def seal(src_dir: str, out_file: str, passphrase: str, name: Optional[str] = None) -> dict:
    src = Path(src_dir)
    if not (src / "metadata" / "djorge.json").is_file():
        raise ValueError(f"{src} não é um artefato DJORGE (falta metadata/djorge.json)")
    data = _tar_dir(src)
    salt, fid = secrets.token_bytes(16), secrets.token_bytes(8)
    key = AESGCM(_key(passphrase, salt))
    man = {"name": name or src.name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
           "file_id": fid.hex(), "format": "djx1"}
    mn = secrets.token_bytes(12)
    mct = key.encrypt(mn, json.dumps(man).encode(), MAGIC)
    with open(out_file, "wb") as f:
        f.write(MAGIC + salt + bytes([N_LOG2]) + struct.pack(">I", len(mn + mct)) + mn + mct)
        for i in range(0, len(data), CHUNK):
            n = secrets.token_bytes(12)
            ct = key.encrypt(n, data[i:i + CHUNK], fid + struct.pack(">Q", i // CHUNK))
            f.write(struct.pack(">I", len(ct)) + n + ct)
    return man


def _read_header(f, passphrase):
    if f.read(4) != MAGIC:
        raise ValueError("arquivo não é um pacote .djx")
    salt, n_log2 = f.read(16), f.read(1)[0]
    (ml,) = struct.unpack(">I", f.read(4))
    blob = f.read(ml)
    key = AESGCM(_key(passphrase, salt, n_log2))
    try:
        man = json.loads(key.decrypt(blob[:12], blob[12:], MAGIC))
    except Exception:
        raise BadKey("chave errada ou arquivo adulterado")
    return key, man


def info(path: str, passphrase: str) -> dict:
    with open(path, "rb") as f:
        return _read_header(f, passphrase)[1]


def open_sealed(path: str, passphrase: str, dest: Optional[str] = None) -> str:
    """Decrypts into a private temporary folder (0700) and returns its path. Verifies every chunk and the hash."""
    with open(path, "rb") as f:
        key, man = _read_header(f, passphrase)
        fid = bytes.fromhex(man["file_id"])
        h, out, i = hashlib.sha256(), io.BytesIO(), 0
        while True:
            hd = f.read(4)
            if not hd:
                break
            (ln,) = struct.unpack(">I", hd)
            n = f.read(12)
            try:
                pt = key.decrypt(n, f.read(ln), fid + struct.pack(">Q", i))
            except Exception:
                raise BadKey(f"bloco {i} adulterado")
            h.update(pt); out.write(pt); i += 1
    if h.hexdigest() != man["sha256"]:
        raise BadKey("conteúdo não confere com o manifesto")
    d = dest or tempfile.mkdtemp(prefix=f"djx-{man['name']}-")
    os.chmod(d, 0o700)
    out.seek(0)
    with tarfile.open(fileobj=out, mode="r") as t:
        for m in t.getmembers():
            p = Path(d, m.name).resolve()
            if not str(p).startswith(str(Path(d).resolve())) or not m.isfile():
                raise ValueError(f"entrada insegura no pacote: {m.name}")
        try:
            t.extractall(d, filter="data")
        except TypeError:                                   # Python < 3.12
            t.extractall(d)
    return d
