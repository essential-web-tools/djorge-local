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
HTML = "https://html.duckduckgo.com/html/?q={q}&kl=br-pt"
LITE = "https://lite.duckduckgo.com/lite/?q={q}&kl=br-pt"
TIMEOUT = 8.0
# User-Agent genérico de navegador: o HTML do DuckDuckGo devolve a página de "anomalia" (sem resultados) para agentes
# desconhecidos, e era isso que deixava a busca vazia.
AGENTE = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 "
          "Safari/537.36")
SAI_DO_COMPUTADOR = True

Transporte = Callable[[str, float], object]


def _http(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": AGENTE, "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.5",
                                               "Accept": "text/html,application/json;q=0.9,*/*;q=0.8"})
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


_TAG_A = re.compile(r"<a\b([^>]*)>(.*?)</a\s*>", re.S | re.I)
_TAG_SN = re.compile(r"<(a|div|td|span)\b([^>]*\bclass\s*=\s*[\"'][^\"']*\bresult[_-]{1,2}snippet\b[^\"']*[\"'][^>]*)>(.*?)</\1\s*>",
                     re.S | re.I)
_HREF = re.compile(r"""\bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.I)
_CLASSE = re.compile(r"""\bclass\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.I)
_ANOMALIA = re.compile(r"anomaly[-_]modal|challenge-form|g-recaptcha|Unfortunately, bots use DuckDuckGo", re.I)


class Bloqueado(Exception):
    """O DuckDuckGo devolveu a página de verificação (sem resultados)."""


def _attr(rx, attrs: str) -> str:
    m = rx.search(attrs or "")
    return next((g for g in (m.groups() if m else ()) if g is not None), "")


def _real(href: str) -> str:
    """Link de redirecionamento do DuckDuckGo (//duckduckgo.com/l/?uddg=<url codificada>&rut=...) -> URL real."""
    href = html.unescape(href or "").strip()
    if href.startswith("//"):
        href = "https:" + href
    q = urllib.parse.urlparse(href)
    if ("duckduckgo.com" in q.netloc or not q.netloc) and q.path.startswith("/l/"):
        u = urllib.parse.parse_qs(q.query).get("uddg")
        if u:
            return u[0]
    return href


def _anuncio(url: str, classes: str) -> bool:
    u = urllib.parse.urlparse(url)
    return ("duckduckgo.com" in u.netloc and (u.path.startswith("/y.js") or "ad_" in u.query)) or \
        "result--ad" in classes or "badge--ad" in classes


def _do_html(raw) -> List[Dict]:
    """Resultados do html.duckduckgo.com (a.result__a + .result__snippet) e do lite (a.result-link + td.result-snippet).
    Atributos em qualquer ordem, aspas simples ou duplas; anúncios e links internos ficam de fora."""
    s = _txt(raw)
    links = []
    for m in _TAG_A.finditer(s):
        cl = _attr(_CLASSE, m.group(1)).split()
        if "result__a" in cl or "result-link" in cl:
            links.append(m)
    out = []
    for i, m in enumerate(links):
        fim = links[i + 1].start() if i + 1 < len(links) else len(s)
        bloco_ini = s.rfind('class="result ', 0, m.start())
        classes = s[bloco_ini:bloco_ini + 120] if bloco_ini >= 0 and m.start() - bloco_ini < 600 else ""
        url = _real(_attr(_HREF, m.group(1)))
        if _anuncio(url, classes):
            continue
        sn = _TAG_SN.search(s, m.end(), fim)
        it = _item(m.group(2), url, sn.group(3) if sn else "")
        if it and "duckduckgo.com" not in it["fonte"]:
            out.append(it)
    if not out and _ANOMALIA.search(s):
        raise Bloqueado("o DuckDuckGo pediu verificação")
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
    for base in (HTML, LITE):                                # o lite só se o HTML falhar ou vier vazio
        if len(achados) >= max(1, min(n, 3)):
            break
        try:
            novos = _do_html(get(base.format(q=qq), timeout))
        except Exception as e:
            erros.append(f"{type(e).__name__}: {e}")
            continue
        achados += novos
        if novos:
            break
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
