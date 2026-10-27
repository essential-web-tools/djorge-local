"""Build step: public-domain illustrations from the Wikimedia Commons open API (engravings, botanical and
anatomical plates, celestial maps, like the Origo site references). Only files whose license metadata says
public domain or CC0 are used. Each image gets the same look (grayscale + light sepia).

    python tools_fetch_images.py            -> djorge_local/static/img/*.webp + index.json + CREDITOS.txt

Fails the build if fewer than MIN_IMAGES could be used, so a silent fallback never ships unnoticed.
"""
import io
import json
import os
import sys
import time
import urllib.parse
import urllib.request

from PIL import Image, ImageEnhance, ImageOps

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "djorge_local", "static", "img")
API = "https://commons.wikimedia.org/w/api.php"
UA = "DjorgeLocalBuild/0.8 (https://github.com/essential-web-tools; build script) python-urllib"
MIN_IMAGES = 6
# name -> Commons file (the references of the Origo site first; extra candidates fill gaps)
WANTED = {
    "flores": "Design_with_Flowers_MET_DP812926.jpg",
    "borboletas": "Drawing_with_Flowers,_Butterflies,_and_Insects_MET_sf-L-1998-60-1.jpg",
    "origem": "Leonardo_-_Bambino_nel_grembo_materno.png",
    "ceu": "Flammarion.jpg",
    "medusas": "Haeckel_Discomedusae_8.jpg",
    "colibris": "Haeckel_Trochilidae.jpg",
    "anatomia": "Vesalius_Fabrica_p190.jpg",
    "rinoceronte": "Dürer's_Rhinoceros,_1515.jpg",
    "cosmos": "Cellarius_ptolemaic_system.jpg",
}
EXTRA_CATEGORIES = ["Category:Kunstformen der Natur (1904)", "Category:Botanical illustrations by Pierre-Joseph Redouté"]
OK_LICENSES = ("public domain", "pd", "cc0", "no restrictions")


def api(params):
    q = urllib.parse.urlencode({**params, "format": "json", "formatversion": "2"})
    req = urllib.request.Request(f"{API}?{q}", headers={"User-Agent": UA})
    return json.load(urllib.request.urlopen(req, timeout=60))


def info(titles):
    r = api({"action": "query", "titles": "|".join(titles), "prop": "imageinfo",
             "iiprop": "url|extmetadata", "iiurlwidth": "1100"})
    out = {}
    for p in r.get("query", {}).get("pages", []):
        ii = (p.get("imageinfo") or [None])[0]
        if not ii:
            continue
        meta = ii.get("extmetadata", {})
        lic = (meta.get("LicenseShortName", {}).get("value") or meta.get("License", {}).get("value") or "").lower()
        out[p["title"]] = {"url": ii.get("thumburl") or ii.get("url"), "page": ii.get("descriptionurl"),
                           "license": lic, "artist": (meta.get("Artist", {}).get("value") or "")[:160]}
    return out


def unify(im):
    im = ImageOps.exif_transpose(im).convert("L")
    im = ImageOps.autocontrast(im, cutoff=1)
    im = ImageEnhance.Contrast(im).enhance(1.06)
    return ImageOps.colorize(im, black=(38, 34, 30), white=(246, 241, 233))


def save(name, meta, credits):
    req = urllib.request.Request(meta["url"], headers={"User-Agent": UA})
    im = unify(Image.open(io.BytesIO(urllib.request.urlopen(req, timeout=90).read())))
    im.thumbnail((1100, 1100))
    im.save(os.path.join(OUT, f"{name}.webp"), "WEBP", quality=78)
    credits.append(f"{name}.webp — licença: {meta['license']} — {meta['page']}")


def main():
    os.makedirs(OUT, exist_ok=True)
    credits, names = [], []
    metas = info(["File:" + f for f in WANTED.values()])
    for name, f in WANTED.items():
        m = next((v for k, v in metas.items() if k.replace(" ", "_") == "File:" + f.replace(" ", "_")), None)
        if not m or not any(x in m["license"] for x in OK_LICENSES):
            print(f"pulei {name}: {'não encontrada' if not m else 'licença ' + m['license']}")
            continue
        try:
            save(name, m, credits); names.append(name); print("ok", name)
        except Exception as e:
            print(f"pulei {name}: {e}")
        time.sleep(0.5)
    for cat in EXTRA_CATEGORIES:                   # fill with plates of the same style if something was missing
        if len(names) >= len(WANTED):
            break
        try:
            r = api({"action": "query", "list": "categorymembers", "cmtitle": cat, "cmtype": "file", "cmlimit": "20"})
            titles = [m["title"] for m in r["query"]["categorymembers"]]
            for t, m in info(titles).items():
                if len(names) >= len(WANTED):
                    break
                if any(x in m["license"] for x in OK_LICENSES):
                    name = f"extra{len(names)}"
                    try:
                        save(name, m, credits); names.append(name); print("ok", name, t)
                    except Exception as e:
                        print("pulei", t, e)
                    time.sleep(0.5)
        except Exception as e:
            print("categoria falhou:", cat, e)
    open(os.path.join(OUT, "CREDITOS.txt"), "w", encoding="utf-8").write(
        "Ilustrações de domínio público (Wikimedia Commons)\n" + "\n".join(credits) + "\n")
    json.dump(names, open(os.path.join(OUT, "index.json"), "w"))
    print(f"{len(names)} imagens")
    if len(names) < MIN_IMAGES:
        sys.exit(f"poucas imagens ({len(names)} < {MIN_IMAGES})")


if __name__ == "__main__":
    main()
