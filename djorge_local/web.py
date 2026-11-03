"""Busca na internet sem chave: API aberta do DuckDuckGo (Instant Answer) + o HTML leve como reserva.

    buscar_web("cotação do dólar", n=6) -> [{"titulo", "url", "trecho", "fonte"}]
    buscar("...")                       -> {"q", "resultados", "erro", "sai_do_computador": True, "ms"}

Isto SAI DO COMPUTADOR (a busca vai para o DuckDuckGo). Timeout de 8 s. O transporte é injetável para testes:
`transporte(url, timeout) -> str|bytes`.
"""
from __future__ import annotations

import html
import json
import re
import time
import urllib.parse
import urllib.request
from typing import Callable, Dict, List, Optional

API = "https://api.duckduckgo.com/?q={q}&format=json&no_html=1&skip_disambig=1"
HTML = "https://html.duckduckgo.com/html/?q={q}"
TIMEOUT = 8.0
AGENTE = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Djorge/2"
SAI_DO_COMPUTADOR = True

Transporte = Callable[[str, float], object]


def _http(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": AGENTE, "Accept-Language": "pt-BR,pt;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(2_000_000)


def _txt(x) -> str:
    if isinstance(x, bytes):
        x = x.decode("utf-8", "replace")
    return str(x or "")


def _limpa(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s or "")
    return " ".join(html.unescape(s).split())


def _fonte(url: str) -> str:
    h = urllib.parse.urlparse(url).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def _item(titulo, url, trecho) -> Optional[Dict]:
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return None
    titulo = _limpa(titulo) or _fonte(url)
    return {"titulo": titulo[:200], "url": url, "trecho": _limpa(trecho)[:400], "fonte": _fonte(url)}


def _da_api(raw) -> List[Dict]:
    try:
        d = json.loads(_txt(raw))
    except ValueError:
        return []
    out = []
    if d.get("AbstractText") and d.get("AbstractURL"):
        out.append(_item(d.get("Heading") or d.get("AbstractSource"), d["AbstractURL"], d["AbstractText"]))
    if d.get("Answer") and isinstance(d["Answer"], str):
        out.append(_item(d.get("Heading") or "Resposta", d.get("AbstractURL") or "https://duckduckgo.com/",
                         d["Answer"]))
    if d.get("Definition") and d.get("DefinitionURL"):
        out.append(_item(d.get("DefinitionSource") or "Definição", d["DefinitionURL"], d["Definition"]))
    for r in d.get("Results") or []:
        out.append(_item(r.get("Text"), r.get("FirstURL"), r.get("Text")))

    def topicos(ts):
        for t in ts or []:
            if t.get("Topics"):
                yield from topicos(t["Topics"])
            elif t.get("FirstURL"):
                txt = t.get("Text") or ""
                yield _item(txt.split(" - ")[0], t["FirstURL"], txt)
    out += list(topicos(d.get("RelatedTopics")))
    return [x for x in out if x]


_A = re.compile(r'<a[^>]+class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S | re.I)
_SN = re.compile(r'class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</(?:a|div|td)>', re.S | re.I)


def _real(href: str) -> str:
    href = html.unescape(href)
    if href.startswith("//"):
        href = "https:" + href
    q = urllib.parse.urlparse(href)
    if "duckduckgo.com" in q.netloc and q.path.startswith("/l/"):
        u = urllib.parse.parse_qs(q.query).get("uddg")
        if u:
            return u[0]
    return href


def _do_html(raw) -> List[Dict]:
    s = _txt(raw)
    links = list(_A.finditer(s))
    out = []
    for i, m in enumerate(links):
        fim = links[i + 1].start() if i + 1 < len(links) else len(s)
        sn = _SN.search(s, m.end(), fim)
        it = _item(m.group(2), _real(m.group(1)), sn.group(1) if sn else "")
        if it and "duckduckgo.com/y.js" not in it["url"]:
            out.append(it)
    return out


def buscar_web(q: str, n: int = 6, transporte: Optional[Transporte] = None, timeout: float = TIMEOUT) -> List[Dict]:
    """Resultados da internet (sem repetir URL). Falha de rede vira lista vazia (veja buscar() para o erro)."""
    return buscar(q, n, transporte, timeout)["resultados"]


def buscar(q: str, n: int = 6, transporte: Optional[Transporte] = None, timeout: float = TIMEOUT) -> Dict:
    t0 = time.perf_counter()
    q = " ".join(str(q or "").split())[:300]
    res = {"q": q, "resultados": [], "erro": None, "sai_do_computador": SAI_DO_COMPUTADOR, "fonte": "DuckDuckGo"}
    if not q:
        return res
    get = transporte or _http
    qq = urllib.parse.quote_plus(q)
    achados, erros = [], []
    try:
        achados = _da_api(get(API.format(q=qq), timeout))
    except Exception as e:                                   # sem rede, timeout, bloqueio
        erros.append(f"{type(e).__name__}: {e}")
    if len(achados) < max(1, min(n, 3)):
        try:
            achados += _do_html(get(HTML.format(q=qq), timeout))
        except Exception as e:
            erros.append(f"{type(e).__name__}: {e}")
    vistos, out = set(), []
    for r in achados:
        k = r["url"].rstrip("/")
        if k in vistos:
            continue
        vistos.add(k)
        out.append(r)
    res["resultados"] = out[:max(1, int(n))]
    if not out and erros:
        res["erro"] = "Não consegui acessar a internet agora. Confira a conexão e tente de novo."
        res["detalhe"] = "; ".join(erros)[:300]
    res["ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return res
