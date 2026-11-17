"""Core Group ARP library for the Plug Store sync: fetch, parse, name, rank, price.

Shared with ~/claude-code/plug-store/core-sync (local tool). No DB access here.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

import openpyxl
import requests

CORE = "https://core.co.za"
PRICE_PAGE = f"{CORE}/pages/arp-arp-price-list-page"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) PlugStoreSync/1.0"}
PROMO_BLUE = "FF0070C0"

# --------------------------------------------------------------------------- fetch

def load_env(path: Path) -> dict:
    env = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def find_pricelist_url(fallback: str | None = None) -> str | None:
    try:
        r = requests.get(PRICE_PAGE, headers=UA, timeout=30)
        m = re.search(r'https?://cdn\.shopify\.com/[^"\'\s]*ARP_Price_list[^"\'\s]*\.xlsx[^"\'\s]*', r.text)
        if m:
            return html.unescape(m.group(0))
    except requests.RequestException:
        pass
    return fallback


def download(url: str) -> Path:
    r = requests.get(url, headers=UA, timeout=60)
    r.raise_for_status()
    if not r.content.startswith(b"PK"):
        raise SystemExit(f"Not an xlsx: {url}")
    d = Path(tempfile.mkdtemp(prefix="core-arp-"))
    p = d / "arp.xlsx"
    p.write_bytes(r.content)
    return p


def get(url, **kw):
    """GET with backoff (Core rate-limits /products.json with 429)."""
    for attempt in range(8):
        r = requests.get(url, headers=UA, timeout=90, **kw)
        if r.status_code != 429:
            r.raise_for_status()
            return r
        time.sleep(int(r.headers.get("Retry-After") or 0) or 10 * (attempt + 1))
    r.raise_for_status()
    return r


def fetch_catalogue() -> dict:
    """SKU -> {images, body, available, handle, title} from Core's public Shopify catalogue."""
    by_sku, page = {}, 1
    while True:
        r = get(f"{CORE}/products.json", params={"limit": 250, "page": page})
        prods = r.json().get("products", [])
        if not prods:
            break
        for p in prods:
            imgs = [i["src"] for i in p.get("images", [])]
            for v in p["variants"]:
                sku = (v.get("sku") or "").strip().upper()
                if not sku:
                    continue
                vimg = (v.get("featured_image") or {}).get("src")
                by_sku[sku] = {
                    "handle": p["handle"], "title": p["title"], "body": p.get("body_html") or "",
                    "images": ([vimg] if vimg else []) + [i for i in imgs if i != vimg],
                    "available": bool(v.get("available")),
                    "price": float(v.get("price") or 0),   # Core wholesale incl. VAT
                }
        page += 1
        time.sleep(2)
    return by_sku


def fetch_bestsellers(pages: int = 12) -> dict:
    """Core storefront best-selling order: product handle -> rank (1 = best)."""
    rank, n = {}, 0
    for page in range(1, pages + 1):
        r = get(f"{CORE}/collections/all", params={"sort_by": "best-selling", "page": page})
        handles = re.findall(r'href="/(?:collections/all/)?products/([^"?#]+)', r.text)
        new = [h for h in dict.fromkeys(handles) if h not in rank]
        if not new:
            break
        for h in new:
            n += 1
            rank[h] = n
        time.sleep(0.5)
    return rank

# --------------------------------------------------------------------------- parse

MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                         "september", "october", "november", "december"], 1)}


def promo_window(path: Path) -> tuple[dt.date | None, dt.date | None, str]:
    """Reads 'X Promotion valid from 16th November 2026 - 30th November 2026' from the list header."""
    ws = openpyxl.load_workbook(path, read_only=True, data_only=True).worksheets[0]
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i > 20:
            break
        for c in row:
            if isinstance(c, str) and "valid from" in c.lower():
                ds = re.findall(r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(\d{4})", c)
                dates = [dt.date(int(y), MONTHS[mo.lower()], int(d)) for d, mo, y in ds if mo.lower() in MONTHS]
                name = c.split("Promotion")[0].strip() if "Promotion" in c else "Core promo"
                if dates:
                    return dates[0], dates[-1], name
    return None, None, ""


def parse_pricelist(path: Path) -> tuple[list[dict], str]:
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.worksheets[0]
    effective, section, items = "", None, []
    for row in ws.iter_rows():
        a, b, c, d, e = (row[i].value for i in range(5))
        if isinstance(b, str) and b.startswith("Effective from"):
            effective = b
        if isinstance(a, str) and isinstance(b, str) and b.startswith("Effective"):
            effective = b
        if isinstance(a, str) and a.startswith("Effective from"):
            effective = a
        if a and b is None and isinstance(a, str):
            section = a.strip()
            continue
        if not (a and b and isinstance(e, (int, float))):
            continue
        font = row[4].font.color.rgb if row[4].font and row[4].font.color is not None else None
        status = str(c or "").strip().upper()
        items.append({
            "sku": str(a).strip().upper(), "name": " ".join(str(b).replace("‑", "-").replace("–", "-").split()),
            "section": section, "status": status,
            "wholesale_excl": float(d or 0), "rrp_incl": float(e),
            "promo": (font == PROMO_BLUE) or status == "WHILE STOCKS LAST",
        })
    return items, effective

# --------------------------------------------------------------------------- naming

FIX = {
    "iphone": "iPhone", "ipad": "iPad", "imac": "iMac", "macbook": "MacBook", "airpods": "AirPods",
    "airtag": "AirTag", "magsafe": "MagSafe", "homepod": "HomePod", "earpods": "EarPods", "usb-c": "USB-C",
    "wi-fi": "Wi-Fi", "wifi": "Wi-Fi", "gb": "GB", "tb": "TB", "ssd": "SSD", "cpu": "CPU", "gpu": "GPU",
    "gps": "GPS", "4k": "4K", "se": "SE", "xdr": "XDR", "a16": "A16", "a17": "A17", "a18": "A18", "a20": "A20",
    "m1": "M1", "m2": "M2", "m3": "M3", "m4": "M4", "m5": "M5", "m6": "M6", "mm": "mm", "s/m": "S/M", "m/l": "M/L",
    "tv": "TV", "usb": "USB", "id": "ID", "finewoven": "FineWoven", "techwoven": "TechWoven", "16e": "16e", "17e": "17e", "and": "and",
    "with": "with", "for": "for", "to": "to", "(1m)": "(1 m)", "neo": "Neo",
}


def pretty(s: str) -> str:
    out = []
    for w in s.lower().split():
        core = w.strip("(),")
        if core in FIX:
            w = w.replace(core, FIX[core])
        elif re.fullmatch(r"\d+(gb|tb)", core):
            w = w.replace(core, core[:-2] + core[-2:].upper())
        elif re.fullmatch(r"\d+mm", core):
            w = w.replace(core, core[:-2] + "mm")
        elif re.fullmatch(r"\d+w", core):
            w = w.replace(core, core.upper())
        elif re.fullmatch(r"\d+-inch", core) or re.fullmatch(r"\d+(\.\d+)?-inch", core):
            pass
        elif re.fullmatch(r"\d+-core", core):
            pass
        else:
            w = w.replace(core, core[:1].upper() + core[1:])
        out.append(w)
    t = " ".join(out)
    t = re.sub(r"\bApple Watch\b", "Apple Watch", t, flags=re.I)
    return t.replace(" With ", " with ").replace(" And ", " and ")


MAC = re.compile(r"^((?:\d+-INCH )?(?:MACBOOK (?:AIR|PRO|NEO)|IMAC|MAC MINI)).*?: APPLE (\S+(?: PRO| MAX| ULTRA)?) CHIP.*?, (\d+GB), (\d+(?:GB|TB)) SSD(.*)$")


def short_title(base: str) -> str:
    m = MAC.match(base)
    if not m:
        return pretty(base).replace("(2 M)", "(2 m)").replace("(1 M)", "(1 m)").replace("(1m)", "(1 m)").replace("(2m)", "(2 m)")
    extra = " + Touch ID" if "TOUCH ID" in m.group(5) else ""
    return f"{pretty(m.group(1))} {m.group(2).title().replace('Pro','Pro').upper() if len(m.group(2))<=3 else pretty(m.group(2))} · {m.group(3)} RAM, {m.group(4)} SSD{extra}"


def slug(s: str) -> str:
    return re.sub(r"(^-|-$)", "", re.sub(r"[^a-z0-9]+", "-", s.lower()))


IPHONE = re.compile(r"^(IPHONE [A-Z0-9 ]+?) (\d+(?:GB|TB)) (.+)$")
IPAD = re.compile(r"^((?:\d+(?:\.\d+)?-INCH )?IPAD(?: AIR| MINI| PRO)? WI-?FI(?: \+ CELLULAR)?) (\d+(?:GB|TB))(.*?) - (.+)$")
WATCH = re.compile(r"^(APPLE WATCH .*?(?:GPS(?: \+ CELLULAR)?) \d+MM) (.+)$")


def group_key(it: dict) -> tuple[str, list[str], list[str]]:
    """-> (base name, option names, option values)."""
    n = it["name"]
    m = IPHONE.match(n)
    if m and not re.search(r"CASE|BUMPER|WALLET|BATTERY", n):
        return m.group(1), ["Storage", "Colour"], [m.group(2), pretty(m.group(3))]
    m = IPAD.match(n)
    if m:
        return (m.group(1) + m.group(3)).replace("WIFI", "WI-FI"), ["Storage", "Colour"], [m.group(2), pretty(m.group(4))]
    m = WATCH.match(n)
    if m:
        return m.group(1), ["Style"], [pretty(m.group(2))]
    parts = re.split(r" - (?=[^-]+$)", n)
    if len(parts) == 2 and len(parts[1]) <= 40:
        val = parts[1].strip()
        name = "Size" if re.fullmatch(r"(S|M|L|S/M|M/L|SMALL|MEDIUM|LARGE)", val) else "Colour"
        return parts[0], [name], [pretty(val)]
    return n, [], []

# --------------------------------------------------------------------------- targeting

EXCLUDE_SECTION = re.compile(r"^(CPU|DISPLAYS|MAC STUDIO|APPLE HOMEVIEW|IPAD AIR 5|IPAD PRO 11\" M4|IPAD AIR M2|IPAD AIR M3|.*EXTENDED WARRANT.*|IPHONE EXTENDED.*)$", re.I)
EXCLUDE_NAME = re.compile(r"EXTENDED WARRANTY|STUDIO DISPLAY|POLISHING CLOTH")
# max product groups per category so the store stays balanced
CAPS = {"iPhone": 9, "Mac": 6, "iPad": 5, "Audio": 5, "Apple Watch": 4, "Accessories": 13}

# Student-market fit (higher = more relevant to SA students/young professionals with
# budget limits). Multiplies Core's real best-seller signal.
FIT = [
    (r"^IPHONE (16E|17E|16|17|15)\b(?! PRO| PLUS)", 2.2),
    (r"^IPHONE AIR", 1.6),
    (r"^IPHONE 17 PRO", 1.4), (r"^IPHONE 16 PLUS", 1.2), (r"^IPHONE 18|^IPHONE DUO", 0.9),
    (r"MACBOOK NEO", 2.6), (r"13-INCH MACBOOK AIR", 2.0), (r"15-INCH MACBOOK AIR", 1.3),
    (r"MACBOOK PRO.*M5 CHIP WITH 10", 0.9), (r"MACBOOK PRO", 0.35),
    (r"^11-INCH IPAD WI", 2.2), (r"IPAD AIR", 1.3), (r"IPAD MINI", 0.9), (r"IPAD PRO", 0.5),
    (r"^AIRPODS 4|^AIRPODS 5", 2.4), (r"^AIRPODS PRO", 2.2), (r"^AIRPODS MAX", 0.7),
    (r"APPLE PENCIL", 1.9), (r"MAGIC KEYBOARD FOLIO|MAGIC KEYBOARD FOR IPAD", 0.9),
    (r"^(20W|30W|35W).*ADAPTER|USB-C .*CABLE|EARPODS|LIGHTNING TO USB", 1.9), (r"(96W|140W|70W).*ADAPTER", 0.8),
    (r"MAGSAFE CHARGER|MAGSAFE BATTERY", 1.4), (r"^AIRTAG \(", 1.8),
    (r"APPLE WATCH SE \d", 1.7), (r"APPLE WATCH SERIES", 1.1), (r"APPLE WATCH ULTRA", 0.4),
    (r"IPHONE 17.*(SILICONE|CLEAR) CASE|IPHONE 16.*(SILICONE|CLEAR) CASE|IPHONE 17E|IPHONE 16E SILICONE", 1.3),
    (r"MAGIC MOUSE|MAGIC KEYBOARD", 1.0), (r"HOMEPOD MINI", 0.9), (r"APPLE TV", 0.6),
    (r"MAC MINI", 0.6), (r"IMAC", 0.35), (r"^HOMEPOD", 0.4),
]


def fit(base: str) -> float:
    for pat, w in FIT:
        if re.search(pat, base):
            return w
    return 0.5


def category(section: str, base: str) -> tuple[str, list[str], str]:
    """-> (product_type, collections, short label)."""
    s = (section or "").upper()
    if base.startswith("IPHONE") and not re.search(r"CASE|BUMPER|WALLET|BATTERY", base):
        return "iPhone", ["Apple", "Phones"], "iPhone"
    if "MACBOOK" in base or "IMAC" in base or "MAC MINI" in base:
        return "Mac", ["Apple", "Laptops & tablets"], "Mac"
    if re.search(r"IPAD", base) and not re.search(r"FOLIO|KEYBOARD|PENCIL|CASE", base):
        return "iPad", ["Apple", "Laptops & tablets"], "iPad"
    if base.startswith("APPLE WATCH") and "CHARG" not in base:
        return "Apple Watch", ["Apple", "Wearables"], "Watch"
    if re.search(r"AIRPODS|EARPODS|HOMEPOD", base):
        return "Audio", ["Apple", "Audio"], "Audio"
    return "Accessories", ["Apple", "Accessories"], "Accessory"

# --------------------------------------------------------------------------- build

def sell_cents(rrp: float, markup: float) -> int:
    return int(math.ceil(round(rrp * (1 + markup / 100), 2))) * 100


def clean_body(body_html: str, limit: int = 900) -> str:
    t = re.sub(r"<li[^>]*>", "\n• ", body_html)
    t = re.sub(r"</p>|<br\s*/?>", "\n", t)
    t = html.unescape(re.sub(r"<[^>]+>", "", t))
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n\s*\n+", "\n\n", t).strip()
    if len(t) > limit:
        t = t[:limit].rsplit("\n", 1)[0].rstrip() + "…"
    return t


def build(items, cat, ranks, markup, top, prev_prices, promo_end=None, promo_name="Core promo"):
    sku_rank = {}
    for sku, c in cat.items():
        if c["handle"] in ranks:
            sku_rank[sku] = ranks[c["handle"]]
    groups = defaultdict(list)
    for it in items:
        if EXCLUDE_SECTION.search(it["section"] or "") or EXCLUDE_NAME.search(it["name"]) or it["rrp_incl"] <= 0:
            continue
        base, onames, ovals = group_key(it)
        it["_onames"], it["_ovals"] = onames, ovals
        groups[(base, tuple(onames))].append(it)

    scored = []
    for (base, onames), its in groups.items():
        best = min((sku_rank.get(i["sku"], 9999) for i in its), default=9999)
        signal = 1.0 / math.log(best + 2) if best < 9999 else 0.12   # unranked -> weak prior
        new_bonus = 1.15 if any(i["status"] == "NEW" for i in its) else 1.0
        promo_bonus = 1.1 if any(i["promo"] for i in its) else 1.0
        score = signal * fit(base) * new_bonus * promo_bonus
        scored.append((score, best, base, list(onames), its))
    scored.sort(key=lambda x: -x[0])

    picked, used = [], defaultdict(int)
    for row in scored:
        ptype = category(row[4][0]["section"], row[2])[0]
        if used[ptype] >= CAPS.get(ptype, 99):
            continue
        used[ptype] += 1
        picked.append(row)
        if len(picked) >= top:
            break
    products, report = [], []
    for pos, (score, best, base, onames, its) in enumerate(picked, 1):
        ptype, colls, label = category(its[0]["section"], base)
        title = short_title(base)
        handle = "apple-" + slug(title)
        if any(i["promo"] for i in its):
            colls = colls + [promo_name.title() + " deals" if promo_name else "Core deals"]
        variants, images, cover_body = [], [], ""
        for i in sorted(its, key=lambda x: (x["rrp_incl"], x["_ovals"])):
            c = cat.get(i["sku"], {})
            price = sell_cents(i["rrp_incl"], markup)
            prev = prev_prices.get(i["sku"])
            compare = prev if prev and prev > price else None
            vimg = (c.get("images") or [None])[0]
            if vimg and vimg not in images:
                images.append(vimg)
            if not cover_body and c.get("body"):
                cover_body = clean_body(c["body"])
            variants.append({
                "sku": i["sku"], "title": " / ".join(i["_ovals"]) or "Default",
                "option_values": i["_ovals"], "price_cents": price, "compare_at_cents": compare,
                "cost_cents": int(round(i["wholesale_excl"] * 1.15 * 100)),
                "track_inventory": False, "inventory_qty": 0, "image_url": vimg,
                "_available": c.get("available", True) and i["status"] != "EOL",
                "_rrp": i["rrp_incl"], "_promo": i["promo"],
            })
        options = []
        for k, n in enumerate(onames):
            vals = list(dict.fromkeys(v["option_values"][k] for v in variants))
            options.append({"name": n, "values": vals})
        promo = any(v["_promo"] for v in variants)
        tags = ["apple", "core-group", label.lower(), f"rank-{pos}"] + (["core-promo", "deal"] if promo else []) \
            + (["new"] if any(i["status"] == "NEW" for i in its) else [])
        intro = (f"Official South African stock from Core, Apple's authorised distributor. "
                 f"Brand new, sealed, with Apple's standard 1-year warranty.")
        if promo:
            intro += (f" {promo_name} price, valid while stocks last"
                      + (f" (until {promo_end.day} {promo_end:%b %Y})." if promo_end else "."))
        products.append({
            "handle": handle, "title": title, "vendor": "Apple", "product_type": ptype,
            "description": intro + ("\n\nSpec: " + pretty(base) if title != pretty(base) else "") + ("\n\n" + cover_body if cover_body else ""),
            "tags": tags, "options": options, "images": images[:8], "collections": colls,
            "variants": variants, "status": "active", "_sort": pos, "_score": round(score, 4), "_best_rank": best,
        })
        lo = min(v["price_cents"] for v in variants) / 100
        report.append(f"{pos:>2}. {title:<70} core-rank {best if best < 9999 else '-':>4}  fit {fit(base):.1f}  "
                      f"from R{lo:,.0f}  {len(variants)} var{'  DEAL' if promo else ''}")
    return products, report

# --------------------------------------------------------------------------- write
