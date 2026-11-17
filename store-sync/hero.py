"""Front Plug Store card picker (cloud copy of ~/claude-code/plug-store/core-sync/hero_pick.py).

Slots: laptop, phone, tablet. Scores every product/variant image for contrast on the white backdrop,
colourfulness and tile fill; rejects white/silver subjects. See skill plug-store-hero-images.
"""
from __future__ import annotations

import io
import math
import re

import requests
from PIL import Image

SLOTS = [
    ("laptop", r"MacBook"),
    ("phone", r"^iPhone [0-9]+[a-z]*( Pro| Pro Max| Plus)?$|^iPhone Air$"),
    ("tablet", r"iPad"),
]
def thumb(url: str, size: int = 320) -> Image.Image | None:
    u = url + ("&" if "?" in url else "?") + f"width={size}" if "cdn.shopify.com" in url else url
    try:
        r = requests.get(u, timeout=30, headers={"User-Agent": "Mozilla/5.0 PlugStoreHeroPick/1.0"})
        r.raise_for_status()
        im = Image.open(io.BytesIO(r.content))
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
            im = Image.alpha_composite(bg, im)
        return im.convert("RGB")
    except Exception:
        return None


def square_cover(im: Image.Image) -> Image.Image:
    s = min(im.size)
    return im.crop(((im.width - s) // 2, (im.height - s) // 2, (im.width + s) // 2, (im.height + s) // 2)).resize((200, 200))


def score(im: Image.Image) -> dict:
    sq = square_cover(im)
    px = list(sq.get_flattened_data()) if hasattr(sq, "get_flattened_data") else list(sq.getdata())
    fg = [p for p in px if min(p) < 238 or (max(p) - min(p)) > 18]   # not the white backdrop
    fill = len(fg) / len(px)
    if not fg:
        return {"score": 0, "fill": 0, "lum": 255, "colour": 0, "reject": "blank"}
    lum = sum(0.299 * r + 0.587 * g + 0.114 * b for r, g, b in fg) / len(fg)
    rg = [r - g for r, g, b in fg]
    yb = [0.5 * (r + g) - b for r, g, b in fg]
    mean = lambda a: sum(a) / len(a)
    sd = lambda a: math.sqrt(max(mean([x * x for x in a]) - mean(a) ** 2, 0))
    colour = math.sqrt(sd(rg) ** 2 + sd(yb) ** 2) + 0.3 * math.sqrt(mean(rg) ** 2 + mean(yb) ** 2)
    contrast = (255 - lum) / 255                      # 0 white .. 1 black
    fill_term = min(fill / 0.45, 1.0)                  # saturates at 45 % of the tile
    s = 0.5 * contrast + 0.3 * min(colour / 60, 1) + 0.2 * fill_term
    reject = "white/silver subject" if lum > 205 else ("too small in tile" if fill < 0.12 else None)
    return {"score": round(s if not reject else 0, 4), "fill": round(fill, 3), "lum": round(lum, 1),
            "colour": round(colour, 1), "reject": reject}


def pick(products: list[dict], log=print) -> list[dict]:
    """products = store_sync_state rows. Returns [{slot, handle, url, score}] (laptop, phone, tablet)."""
    winners, used = [], set()
    for slot, pat in SLOTS:
        best = None
        for p in products:
            if p["status"] != "active" or p["handle"] in used or not re.search(pat, p["title"]):
                continue
            urls = list(dict.fromkeys((p.get("images") or []) + [v["image_url"] for v in p.get("variants", [])
                                                                  if v.get("is_active") and v.get("image_url")]))[:12]
            pop = 1 + 0.25 / (1 + (p.get("sort") or 100) / 10)
            for u in urls:
                im = thumb(u)
                if im is None:
                    continue
                sc = score(im)
                final = sc["score"] * pop
                if best is None or final > best["score"]:
                    best = {"slot": slot, "handle": p["handle"], "url": u, "score": round(final, 4),
                            "images": p.get("images") or [], "title": p["title"]}
        if best and best["score"] > 0:
            winners.append(best)
            used.add(best["handle"])
            log(f"hero {slot}: {best['title']} ({best['score']})")
    return winners
