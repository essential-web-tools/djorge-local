"""Build step: downloads PUBLIC-DOMAIN engravings (Wikimedia Commons / The Met Open Access, CC0 or expired
copyright) and gives them one unified look (grayscale + light sepia), as in the Origo site.
Writes djorge_local/static/img/<name>.webp and CREDITOS.txt. Missing images are skipped: the interface has
CSS ornaments as fallback, so the app never depends on these files.

    python tools_fetch_images.py
"""
import io
import json
import os
import sys
import urllib.parse
import urllib.request

from PIL import Image, ImageEnhance, ImageOps

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "djorge_local", "static", "img")
IMAGES = {   # name -> (Commons file, credit)
    "flores": ("Design_with_Flowers_MET_DP812926.jpg", "Design with Flowers — The Met, Open Access (CC0)"),
    "borboletas": ("Drawing_with_Flowers,_Butterflies,_and_Insects_MET_sf-L-1998-60-1.jpg",
                   "Drawing with Flowers, Butterflies, and Insects — The Met, Open Access (CC0)"),
    "origem": ("Leonardo_-_Bambino_nel_grembo_materno.png", "Leonardo da Vinci, estudo do feto (~1510) — domínio público"),
    "ceu": ("Flammarion.jpg", "Gravura Flammarion (1888) — domínio público"),
    "medusas": ("Haeckel_Discomedusae_8.jpg", "Ernst Haeckel, Kunstformen der Natur (1904) — domínio público"),
    "colibris": ("Haeckel_Trochilidae.jpg", "Ernst Haeckel, Kunstformen der Natur (1904) — domínio público"),
}
UA = "DjorgeLocalBuild/0.7 (https://github.com/essential-web-tools/djorge-local)"


def unify(im: Image.Image) -> Image.Image:
    im = ImageOps.exif_transpose(im).convert("L")
    im = ImageOps.autocontrast(im, cutoff=1)
    im = ImageEnhance.Contrast(im).enhance(1.06)
    return ImageOps.colorize(im, black=(38, 34, 30), white=(246, 241, 233))    # sépia suave, igual em todas


def main():
    os.makedirs(OUT, exist_ok=True)
    credits, ok = [], 0
    for name, (fname, credit) in IMAGES.items():
        url = "https://commons.wikimedia.org/wiki/Special:FilePath/" + urllib.parse.quote(fname) + "?width=1100"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            data = urllib.request.urlopen(req, timeout=60).read()
            im = unify(Image.open(io.BytesIO(data)))
            im.thumbnail((1100, 1100))
            im.save(os.path.join(OUT, f"{name}.webp"), "WEBP", quality=78)
            credits.append(f"{name}.webp — {credit} — https://commons.wikimedia.org/wiki/File:{fname}")
            ok += 1
        except Exception as e:
            print(f"pulei {name}: {e}", file=sys.stderr)
    open(os.path.join(OUT, "CREDITOS.txt"), "w", encoding="utf-8").write("\n".join(credits) + "\n")
    json.dump(sorted(n for n in IMAGES if os.path.exists(os.path.join(OUT, f"{n}.webp"))),
              open(os.path.join(OUT, "index.json"), "w"))
    print(f"{ok}/{len(IMAGES)} imagens")


if __name__ == "__main__":
    main()
