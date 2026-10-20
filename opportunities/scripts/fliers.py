#!/usr/bin/env python3
"""Campus Plug — render a social flier for every collected opportunity.

Fills assets/flier/template.html (the Claude Design "Opportunity Flier" layout)
once per format and screenshots it with headless Chrome:

    portrait  1080x1350   Instagram / Facebook feed, LinkedIn
    square    1080x1080   X, WhatsApp (requirements block hidden, as designed)
    story     1080x1920   Stories, TikTok, WhatsApp Status

Each PNG is named for where it goes, e.g.
    aramex-4d2b1d-portrait-instagram-facebook-linkedin-feed.png

    python3 fliers.py                      # every listing in out/latest.json
    python3 fliers.py --limit 3
    python3 fliers.py --only d3f51ad8      # one listing, by id prefix
    python3 fliers.py --formats portrait,story --no-enrich

A flier is a teaser: it never carries the apply link, only the Play Store QR.
Every fact on it is copied from the feed or verified against the listing page
(see enrich.py). A slot with no known fact says "See the app" - never a guess.
"""
import argparse
import html
import json
import re
import io
import subprocess
import sys
import tempfile
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from PIL import Image

import enrich as enrichlib
import fetchlib
from email_digest import refresh_deadlines
from posts import APP_NAME, _tags, search_term

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets" / "flier"
CHROME = "/usr/bin/google-chrome"

PLAY_URL = "https://play.google.com/store/apps/details?id=co.qconnec.campusplug"
HANDLE = "@campusplugsa"     # planned handle - change here if another is registered
FOOTER_LEFT = "Verified students only · Your Campus, Plugged In."
CTA_HEADLINE = "Full details & apply link on CampusPlug"
CTA_SUB = "Free for students. Tap the link in bio or scan to download."
SEE_APP = "See the app"

LOGO_DIR = ROOT / "state" / "logos"

# What each format is for - this goes into the file name so the right image
# is obvious when posting from a phone.
PLATFORMS = {"portrait": "instagram-facebook-linkedin-feed", "square": "x-whatsapp",
             "story": "stories-tiktok-whatsapp-status"}
FORMATS = {"portrait": (1080, 1350, 24), "square": (1080, 1080, 24), "story": (1080, 1920, 40)}

ACCENTS = {
    "internship": "var(--orange-500)", "bursary": "var(--gold-500)",
    "learnership": "var(--orange-300)", "apprenticeship": "var(--green-500)",
    "in-service training": "var(--gold-300)", "graduate programme": "var(--orange-400)",
}
# The pill is one line, so the two long category names get a short form.
TYPE_LABEL = {"in-service training": "In-Service", "graduate programme": "Grad Programme"}

_PROGRAMME = re.compile(r"intern|learner|bursar|apprentice|graduate|programme|trainee|training|scholar", re.I)
_DANGLING = re.compile(r"\b(in|of|or|and|the|a|an|to|for|with|as|at)\W*$", re.I)
_YEARS = re.compile(r"\b20\d\d(?:\s*[/-]\s*(?:20)?\d\d)?\b")


# ------------------------------------------------------------------ flier copy

def clean_title(item):
    """(title, intake) - the listing title without site noise, and its year."""
    title = re.sub(r"\s+New$", "", item["title"]).strip()
    years = _YEARS.search(title)
    intake = re.sub(r"\s*([/-])\s*", r" \1 ", years.group(0)) if years else ""
    title = _YEARS.sub("", title)
    # Sources cut long titles mid-bracket ("Internships (Supply Chain, Logi...").
    # A bracket that never closes, or that pushes the title over length, goes.
    title = re.sub(r"\s*\([^)]*$", "", title)
    if len(title) > 60:
        title = re.sub(r"\s*\([^)]*\)", "", title)
    title = re.sub(r"\s+([:,])", r"\1", re.sub(r"\s{2,}", " ", title)).strip(" :-–|/.…")
    # The employer has its own line. "Employer: Programme" keeps the programme;
    # "Programme: 80 posts, 12 months" keeps the programme and drops the detail.
    employer = (item.get("employer") or "").lower()
    head, sep, tail = title.partition(": ")
    if sep and _PROGRAMME.search(head):
        title = head
    elif sep:
        title = tail
    if employer and title.lower().startswith(employer):
        title = title[len(employer):].strip(" :-–|") or title
    if len(title) > 60:
        title = title[:60].rsplit(" ", 1)[0].rstrip(" ,:-–&") + "…"
    return title, intake


def flier_values(item, extra):
    title, intake = clean_title(item)
    category = item.get("primary_category")

    if extra.get("value"):
        value_label, value = extra["value_label"], extra["value"]
    elif item.get("fields"):
        value_label, value = "Field", item["fields"][0]
    elif item.get("province"):
        value_label, value = "Where", item["province"]
    else:
        value_label, value = "Details", SEE_APP

    if item.get("closing_date"):
        deadline = date.fromisoformat(item["closing_date"]).strftime("%-d %b %Y")
    else:
        deadline = SEE_APP

    if extra.get("eligibility"):
        eligibility = extra["eligibility"][0].upper() + extra["eligibility"][1:]
    elif item.get("matric_friendly"):
        eligibility = "Matric & up"
    else:
        eligibility = SEE_APP

    # Verbatim phrases can still be fragments ("A completed Degree or Diploma
    # in", "unemployed"). Those are dropped rather than printed half-finished.
    reqs = [r for r in extra.get("requirements") or []
            if len(r.split()) >= 3 and not _DANGLING.search(r)]
    more = len(reqs) - 2
    return {
        "type": TYPE_LABEL.get(category) or (category or "opportunity").title(),
        "accent": ACCENTS.get(category, "var(--orange-500)"),
        "intake": intake,
        "title": title,
        "provider": item.get("employer") or "",
        "place": item.get("province") or "",
        "playUrlText": PLAY_URL.replace("https://", ""),
        "valueLabel": value_label, "value": value,
        "deadline": deadline, "eligibility": eligibility,
        "reqs": reqs[:2],
        # A count is only printed when it is a believable requirement list;
        # a page that lists 20 study fields is not "20 requirements".
        "lockedLine": (f"+{more} more requirements & how to apply on the app" if 0 < more <= 8
                       else "More requirements & how to apply on the app"),
        "ctaHeadline": CTA_HEADLINE, "ctaSub": CTA_SUB,
        "footerLeft": FOOTER_LEFT, "handle": HANDLE,
    }


def caption(item):
    """Post caption per the design's guide: Play Store link first, then the pull."""
    cat = item.get("primary_category") or "opportunity"
    who = item.get("employer")
    line = f"{who} {cat} is open." if who else f"New {cat} is open."
    if item.get("closing_date"):
        line += f" Closes {date.fromisoformat(item['closing_date']).strftime('%-d %B %Y')}."
    return "\n".join([
        PLAY_URL,
        line,
        f'Search "{search_term(item)}" on {APP_NAME} for the requirements and how to apply.',
        " ".join(_tags(item, 5)),
    ])


# ------------------------------------------------------------------- rendering

def fill(template, vals, fmt):
    w, h, gap = FORMATS[fmt]
    story = fmt == "story"
    title_size = (72 if story else 62) if len(vals["title"]) > 48 else (84 if story else 74)

    def block(name, keep, text):
        pat = re.compile(rf"<!--if:{name}-->(.*?)<!--endif:{name}-->", re.S)
        return pat.sub(lambda m: m.group(1) if keep else "", text)

    out = block("requirements", fmt != "square" and bool(vals["reqs"]), template)
    out = block("logo", bool(vals.get("logoSrc")), out)
    loop = re.compile(r"<!--for:req-->(.*?)<!--endfor:req-->", re.S)
    out = loop.sub(lambda m: "".join(m.group(1).replace("{{ r }}", html.escape(r))
                                     for r in vals["reqs"]), out)
    slots = {k: html.escape(str(v)) for k, v in vals.items() if k != "reqs"}
    slots.setdefault("logoSrc", "")
    slots.update(w=w, h=h, gap=gap, titleSize=title_size, base=ASSETS.as_uri() + "/",
                 providerSize=44 if len(vals["provider"]) > 30 else 54,
                 accent=vals["accent"])
    return re.sub(r"\{\{ (\w+) \}\}", lambda m: str(slots[m.group(1)]), out)


def screenshot(page_html, fmt, dest, workdir):
    w, h, _ = FORMATS[fmt]
    src = Path(workdir) / f"{dest.stem}.html"
    src.write_text(page_html, encoding="utf-8")
    proc = subprocess.run(
        [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
         "--force-device-scale-factor=1", f"--window-size={w},{h}",
         "--virtual-time-budget=4000", f"--user-data-dir={workdir}/profile",
         f"--screenshot={dest}", src.as_uri()],
        capture_output=True, text=True, timeout=90)
    if not dest.exists():
        raise RuntimeError(f"chrome produced no image: {proc.stderr.strip()[-300:]}")


def find_logo(item, log):
    """Path to the provider's logo as shown on the listing page, or None.

    Only an image the page itself ties to the provider counts: Graduates24's
    og:image under /logos/, or an <img> whose alt text is the employer's name.
    The listing site's own logo and generic share banners are never used.
    """
    dest = LOGO_DIR / f"{item['id']}.png"
    miss = LOGO_DIR / f"{item['id']}.none"
    if dest.exists():
        return dest
    if miss.exists() or not item.get("employer"):
        return None
    LOGO_DIR.mkdir(parents=True, exist_ok=True)
    page = fetchlib.get(item["url"], log=log, retries=0)
    if not page:
        return None                       # not marked: the site may answer tomorrow
    url = None
    og = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', page)
    if og and "/logos/" in og.group(1):
        url = og.group(1)
    else:
        employer = item["employer"].strip().lower()
        for tag in re.findall(r"<img[^>]+>", page):
            alt = re.search(r'alt=["\']([^"\']*)', tag)
            src = re.search(r'src=["\'](https?://[^"\']+)', tag)
            if alt and src and alt.group(1).strip().lower() == employer:
                url = src.group(1)
                break
    try:
        if not url:
            raise ValueError("no provider logo on the page")
        req = urllib.request.Request(html.unescape(url), headers={"User-Agent": fetchlib.BROWSER_UA})
        raw = urllib.request.urlopen(req, timeout=20).read()
        img = Image.open(io.BytesIO(raw))
        if min(img.size) < 40:
            raise ValueError("image too small to be a logo")
        img.convert("RGBA").save(dest)
        return dest
    except Exception as exc:  # noqa: BLE001 - a flier without a logo is still a flier
        miss.write_text(str(exc), encoding="utf-8")
        return None


def slug(item):
    name = item.get("employer") or item["title"]
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] + "-" + item["id"][:6]


# ------------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--feed", default=str(ROOT / "out" / "latest.json"))
    ap.add_argument("--limit", type=int, help="render only the first N listings")
    ap.add_argument("--only", help="render only the listing whose id starts with this")
    ap.add_argument("--formats", default="portrait,square,story")
    ap.add_argument("--no-enrich", action="store_true",
                    help="skip reading listing pages; feed facts only")
    ap.add_argument("--out-dir", help="default: out/fliers-<date>")
    args = ap.parse_args()

    def log(m):
        print(m, flush=True)

    feed = json.loads(Path(args.feed).read_text(encoding="utf-8"))
    when = feed.get("collected_for", date.today().isoformat())
    items = refresh_deadlines(feed, log)
    # Fewest days left first - that is the order they should be posted in.
    items.sort(key=lambda i: i["days_left"] if i.get("days_left") is not None else 10**6)
    if args.only:
        items = [i for i in items if i["id"].startswith(args.only)]
    if args.limit:
        items = items[:args.limit]
    if not items:
        log("no open listings - no fliers rendered")
        return 0

    formats = [f.strip() for f in args.formats.split(",") if f.strip()]
    unknown = [f for f in formats if f not in FORMATS]
    if unknown:
        log(f"unknown format(s): {', '.join(unknown)}")
        return 2

    out_dir = Path(args.out_dir) if args.out_dir else ROOT / "out" / f"fliers-{when}"
    out_dir.mkdir(parents=True, exist_ok=True)
    template = (ASSETS / "template.html").read_text(encoding="utf-8")

    # Reading the listing pages is the slow part (a model call each), so do
    # them side by side; rendering stays sequential.
    extras, logos = {}, {}
    if not args.no_enrich:
        with ThreadPoolExecutor(max_workers=4) as pool:
            extras = dict(zip((i["id"] for i in items),
                              pool.map(lambda i: enrichlib.enrich(i, log), items)))
            logos = dict(zip((i["id"] for i in items),
                             pool.map(lambda i: find_logo(i, log), items)))

    manifest, failed = [], 0
    with tempfile.TemporaryDirectory() as work:
        for item in items:
            extra = extras.get(item["id"], {})
            vals = flier_values(item, extra)
            if logos.get(item["id"]):
                vals["logoSrc"] = logos[item["id"]].as_uri()
            name, files = slug(item), {}
            for fmt in formats:
                dest = out_dir / f"{name}-{fmt}-{PLATFORMS[fmt]}.png"
                try:
                    screenshot(fill(template, vals, fmt), fmt, dest, work)
                    files[fmt] = dest.name
                except Exception as exc:  # noqa: BLE001 - one bad flier must not stop the rest
                    failed += 1
                    log(f"  ! {name} {fmt}: {exc}")
            if files:
                manifest.append({
                    "id": item["id"], "title": item["title"],
                    "closing_date": item.get("closing_date"), "days_left": item.get("days_left"),
                    "search_term": search_term(item), "files": files,
                    "logo": bool(logos.get(item["id"])),
                    "enriched": [k for k in ("value", "eligibility", "requirements") if extra.get(k)],
                    "caption": caption(item),
                })
            log(f"  {name}: {len(files)} flier(s)"
                + (f", page facts: {', '.join(manifest[-1]['enriched'])}" if files and manifest[-1]["enriched"] else ""))

    (out_dir / "manifest.json").write_text(
        json.dumps({"generated_for": when, "play_url": PLAY_URL, "fliers": manifest},
                   indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"wrote {sum(len(m['files']) for m in manifest)} fliers for "
        f"{len(manifest)} listings to {out_dir}" + (f" ({failed} failed)" if failed else ""))
    return 1 if failed and not manifest else 0


if __name__ == "__main__":
    sys.exit(main())
