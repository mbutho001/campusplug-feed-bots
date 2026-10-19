"""One adapter per site. Each returns raw listing dicts; classification and
deduplication happen later in collect.py.

Every adapter reports failure honestly by returning (items, error). A source
that returns nothing because it broke must never look like a source that
returned nothing because there was nothing new.
"""
import re

from fetchlib import get, parse_feed, strip_tags, unwrap

# Sites whose RSS carries real listings. internships.co.za's feed is a blog
# feed, so that site is read from its listing pages instead (see HTML_SOURCES).
FEED_SOURCES = [
    {"key": "puffandpass",   "name": "Puff and Pass",
     "url": "https://www.puffandpass.co.za/feed",
     "site": "https://www.puffandpass.co.za"},
    {"key": "graduates24",   "name": "Graduates24",
     "url": None, "site": "https://www.graduates24.com"},   # HTML, handled below
    {"key": "internshipsza", "name": "Internships in South Africa",
     "url": "https://internshipsza.co.za/feed/",
     "site": "https://internshipsza.co.za"},
    {"key": "nasi-ispani",   "name": "Nasi iSpani",
     "url": "https://www.nasi-ispani.co.za/feed/",
     "site": "https://www.nasi-ispani.co.za"},
    {"key": "zabursaries",   "name": "Bursaries in South Africa (ZABursaries)",
     "url": "https://www.zabursaries.co.za/feed/",
     "site": "https://www.zabursaries.co.za"},
    {"key": "bursaries-sa",  "name": "Bursaries South Africa",
     "url": "https://bursaries-southafrica.co.za/feed/",
     "site": "https://bursaries-southafrica.co.za"},
]

FEED_SOURCES = [s for s in FEED_SOURCES if s["url"]]

# Graduates24 listing pages. Cards carry title, location, posted and closing
# dates, which makes this the richest single source.
G24_PAGES = [
    ("https://www.graduates24.com/learnerships?sort=recently_added", "learnership"),
    ("https://www.graduates24.com/internshipprogrammes?sort=recently_added", "internship"),
    ("https://www.graduates24.com/bursaries?sort=recently_added", "bursary"),
    ("https://www.graduates24.com/graduate_programmes?sort=recently_added", "graduate programme"),
    ("https://www.graduates24.com/vacationwork?sort=recently_added", "in-service training"),
]

# Cloudflare turns anonymous HTTP away with a 403 on this host. It is not
# broken - it just cannot be read from a script. collect.py reports it as a
# manual source so the SKILL.md WebFetch step can fill the gap.
BLOCKED_SOURCES = [
    {"key": "careersportal", "name": "Careers Portal",
     "site": "https://www.careersportal.co.za",
     "reason": "Cloudflare returns HTTP 403 to scripted requests",
     "pages": ["https://www.careersportal.co.za/learnerships",
               "https://www.careersportal.co.za/internships",
               "https://www.careersportal.co.za/bursaries"]},
]


def from_feed(src, log):
    xml = get(src["url"], log=log)
    if xml is None:
        return [], "feed unreachable"
    items = parse_feed(xml)
    if not items:
        return [], "feed parsed to zero items (format may have changed)"
    out = []
    for it in items:
        if not it.get("link") or not it.get("title"):
            continue
        out.append({
            "source_key": src["key"],
            "source_name": src["name"],
            "title": it["title"],
            "url": it["link"],
            "published_raw": it.get("published", ""),
            "source_categories": it.get("categories", []),
            "body": it.get("body", "")[:4000],
            "location_raw": None,
            "closing_raw": None,
        })
    return out, None


_CARD_RE = re.compile(r'<div class="g24-job-card".*?(?=<div class="g24-job-card"|</body>)', re.S)


def _card_field(card, pattern):
    m = re.search(pattern, card, re.S | re.I)
    return strip_tags(unwrap(m.group(1))) if m else None


def from_graduates24(log):
    """Scrape the Graduates24 category pages into listing dicts."""
    out, errors = [], []
    for url, hint in G24_PAGES:
        html_text = get(url, log=log)
        if html_text is None:
            errors.append(url.rsplit("/", 1)[-1].split("?")[0])
            continue
        cards = _CARD_RE.findall(html_text)
        if not cards:
            errors.append(f"{url.rsplit('/', 1)[-1].split('?')[0]} (no cards - markup changed?)")
            continue
        for card in cards:
            link = re.search(r'href="(https://www\.graduates24\.com/[^"]+)"\s+class="g24-card-link"', card)
            title = _card_field(card, r'class="g24-card-title[^"]*">(.*?)</h2>')
            if not link or not title:
                continue
            meta = strip_tags(card)
            closes = re.search(r"Closes:\s*([0-9]{1,2}\s+\w+\s+[0-9]{4})", meta, re.I)
            posted = re.search(r"Posted:\s*([0-9]{1,2}\s+\w+\s+[0-9]{4})", meta, re.I)
            loc = _card_field(card, r'fa-map-marker"></i>(.*?)</span>')
            out.append({
                "source_key": "graduates24",
                "source_name": "Graduates24",
                "title": title,
                "url": link.group(1),
                "published_raw": posted.group(1) if posted else "",
                "source_categories": [hint],
                "body": _card_field(card, r'class="g24-card-excerpt">(.*?)</p>') or "",
                "location_raw": loc.strip() if loc else None,
                # A card's own "Closes:" line beats anything parsed out of prose.
                "closing_raw": closes.group(1) if closes else None,
            })
    return out, ("; ".join(errors) if errors else None)


def collect_all(log):
    """Run every scriptable source. Returns (items, failures)."""
    items, failures = [], []
    for src in FEED_SOURCES:
        log(f"  - {src['name']}")
        got, err = from_feed(src, log)
        if err:
            failures.append({"name": src["name"], "reason": err})
        log(f"    {len(got)} listings")
        items.extend(got)

    log("  - Graduates24")
    got, err = from_graduates24(log)
    if err:
        failures.append({"name": "Graduates24", "reason": f"partial: {err}"})
    log(f"    {len(got)} listings")
    items.extend(got)
    return items, failures
