"""Multimodal input -> text. The models only read text; images, PDFs, documents and audio are converted first.
Every converter is optional: if its library is missing, the file is skipped with a clear reason.

  text  .txt .md .csv .json .log .html          (built-in)
  email .eml                                    (built-in, subject + plain body)
  pdf   .pdf        pip install pypdf
  docx  .docx       pip install python-docx
  image .png .jpg   pip install pytesseract pillow  (+ Tesseract installed on the system)
  audio .wav .mp3 .m4a .ogg   pip install faster-whisper   (extract.audio: true)
"""
from __future__ import annotations

import email
import html
import re
from email import policy
from pathlib import Path

TEXT = {".txt", ".md", ".csv", ".json", ".log", ".xml", ".yaml", ".yml"}
IMAGE = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
AUDIO = {".wav", ".mp3", ".m4a", ".ogg", ".flac"}
_whisper = None


class Unsupported(Exception):
    pass


def html_to_text(s: str) -> str:
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</h\d>", "\n", s)
    return re.sub(r"[ \t]+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def from_eml(raw: bytes) -> dict:
    msg = email.message_from_bytes(raw, policy=policy.default)
    body = msg.get_body(preferencelist=("plain", "html"))
    txt = body.get_content() if body else ""
    if body is not None and body.get_content_type() == "text/html":
        txt = html_to_text(txt)
    return {"title": str(msg.get("subject", "")), "sender": str(msg.get("from", "")),
            "text": f"Assunto: {msg.get('subject', '')}\nDe: {msg.get('from', '')}\n\n{txt}"}


def from_file(path: str, cfg: dict) -> dict:
    p = Path(path)
    ext = p.suffix.lower()
    mx = cfg["extract"]["max_chars"]
    if ext in TEXT:
        t = p.read_text(encoding="utf-8", errors="replace")
    elif ext in (".html", ".htm"):
        t = html_to_text(p.read_text(encoding="utf-8", errors="replace"))
    elif ext == ".eml":
        d = from_eml(p.read_bytes()); d["text"] = d["text"][:mx]; d["kind"] = "email"
        return d
    elif ext == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            raise Unsupported("PDF: pip install pypdf")
        t = "\n".join((pg.extract_text() or "") for pg in PdfReader(str(p)).pages[:200])
    elif ext == ".docx":
        try:
            import docx
        except ImportError:
            raise Unsupported("DOCX: pip install python-docx")
        t = "\n".join(par.text for par in docx.Document(str(p)).paragraphs)
    elif ext in IMAGE:
        if not cfg["extract"]["ocr"]:
            raise Unsupported("OCR desligado (extract.ocr)")
        try:
            import pytesseract
            from PIL import Image
        except ImportError:
            raise Unsupported("imagem: pip install pytesseract pillow + Tesseract no sistema")
        t = pytesseract.image_to_string(Image.open(p), lang=cfg["extract"]["ocr_lang"])
    elif ext in AUDIO:
        if not cfg["extract"]["audio"]:
            raise Unsupported("áudio desligado (extract.audio)")
        global _whisper
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise Unsupported("áudio: pip install faster-whisper")
        _whisper = _whisper or WhisperModel(cfg["extract"]["whisper_model"], device="cpu", compute_type="int8")
        segs, _ = _whisper.transcribe(str(p), language=cfg["language"][:2])
        t = " ".join(s.text for s in segs)
    else:
        raise Unsupported(f"tipo não suportado: {ext}")
    return {"title": p.name, "text": t[:mx], "kind": "file"}
