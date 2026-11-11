"""One adapter per site. Each returns raw listing dicts; classification and
deduplication happen later in collect.py.

Every adapter reports failure honestly by returning (items, error). A source
that returns nothing because it broke must never look like a source that
returned nothing because there was nothing new.
"""
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request

from fetchlib import BROWSER_UA, get, parse_feed, strip_tags, unwrap

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
    # Website behind the "Government Vacancies SA" WhatsApp channel
    # (whatsapp.com/channel/0029VaojqHIBadmcYXHyzZ3N). WhatsApp channel posts
    # cannot be read from the web, so the channel's own site is read instead.
    # Its feed holds 10 posts and the site posts more than that a day, hence
    # several pages. allow_jobs: plain vacancies are kept as category "job".
    {"key": "govvacancies",  "name": "Government Vacancies SA",
     "url": "https://governmentvacancies.co.za/feed/",
     "site": "https://governmentvacancies.co.za",
     "pages": 3, "allow_jobs": True},
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
    for page in range(2, src.get("pages", 1) + 1):
        more = get(f"{src['url']}?paged={page}", log=log)
        items.extend(parse_feed(more) if more else [])
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
            "allow_jobs": bool(src.get("allow_jobs")),
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


# ------------------------------------------------------------------- DPSA
# The official "governmentza" WhatsApp channel
# (whatsapp.com/channel/0029Va7RhXFGZNCyCpBSja0A) posts its job openings as the
# weekly DPSA Public Service Vacancy Circular. The circular page splits into one
# PDF per department/province; each becomes one listing.
DPSA = "https://www.dpsa.gov.za"
DPSA_INDEX = DPSA + "/newsroom/psvc/"


def _get_bytes(url, timeout=60):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception:  # noqa: BLE001
        return None


def _ensure_pdftotext(log):
    """GitHub's runner may lack poppler; install it on demand (passwordless sudo there)."""
    if shutil.which("pdftotext"):
        return True
    if not (os.environ.get("CI") and shutil.which("apt-get")):
        return False
    log("    installing poppler-utils for pdftotext")
    env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
    try:
        subprocess.run(["sudo", "apt-get", "update", "-qq"], check=True, timeout=300,
                       env=env, capture_output=True)
        subprocess.run(["sudo", "apt-get", "install", "-y", "-qq", "--no-install-recommends",
                        "poppler-utils"], check=True, timeout=600, env=env, capture_output=True)
    except Exception as exc:  # noqa: BLE001
        log(f"    ! poppler install failed: {exc}")
    return bool(shutil.which("pdftotext"))


def _pdf_text(url):
    data = _get_bytes(url)
    if not data or not data.startswith(b"%PDF"):
        return None
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(data)
        f.flush()
        try:
            r = subprocess.run(["pdftotext", "-layout", f.name, "-"], capture_output=True,
                               text=True, timeout=120)
        except Exception:  # noqa: BLE001
            return None
    return r.stdout if r.returncode == 0 else None


_DPSA_DATE = r"(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+20\d\d)"


def _dpsa_section(text):
    """Post titles, distinct closing dates and a Grade-12 flag from one annexure."""
    titles = []
    for m in re.finditer(r"^\s*POST\s+\d+/\d+\s*:\s*(.+)$", text, re.M):
        t = re.split(r"\s+REF\b|\s+\(X\d", m.group(1), maxsplit=1)[0]
        t = re.sub(r"\s+", " ", t).strip(" :-")
        if t and t.title() not in titles:
            titles.append(t.title())
    closes = []
    for m in re.finditer(r"CLOSING\s+DATE\s*:\s*" + _DPSA_DATE, text, re.I):
        d = re.sub(r"\s+", " ", m.group(1))
        if d not in closes:
            closes.append(d)
    posts = len(re.findall(r"^\s*POST\s+\d+/\d+\s*:", text, re.M))
    grade12 = bool(re.search(r"grade\s*12|matric|national\s+senior\s+certificate", text, re.I))
    return titles, closes, posts, grade12


def from_dpsa(log):
    """Newest weekly Public Service Vacancy Circular -> one listing per department."""
    index = get(DPSA_INDEX, log=log)
    if index is None:
        return [], "circular index unreachable"
    nums = [(int(y), int(n)) for n, y in
            re.findall(r"/newsroom/psvc/circular-(\d+)-of-(20\d\d)/", index)]
    if not nums:
        return [], "no circular links on index (markup changed?)"
    year, num = max(nums)
    page_url = f"{DPSA_INDEX}circular-{num}-of-{year}/"
    page = get(page_url, log=log)
    if page is None:
        return [], f"circular {num} of {year} unreachable"
    posted = re.search(r"Posting Date:.*?" + _DPSA_DATE, strip_tags(page), re.S | re.I)
    sections = re.findall(
        r"<a [^>]*href=[\"']([^\"']*/vacancies/%d/%d/[a-z]+\.pdf)[\"'][^>]*>(.*?)</a>" % (year, num),
        page, re.S | re.I)
    if not sections:
        return [], f"circular {num} of {year}: no department sections (markup changed?)"
    can_read = _ensure_pdftotext(log)
    out, errors = [], []
    provincial = re.compile(r"^(Gauteng|Kwazulu|KwaZulu|Limpopo|Mpumalanga|North West|"
                            r"Northern Cape|Western Cape|Eastern Cape|Free State)", re.I)
    for href, name in sections:
        name = re.sub(r"\s+", " ", strip_tags(name)).strip()
        url = href if href.startswith("http") else DPSA + href
        titles, closes, posts, grade12 = [], [], 0, False
        if can_read:
            text = _pdf_text(url)
            if text is None:
                errors.append(name)
            else:
                titles, closes, posts, grade12 = _dpsa_section(text)
        name = name.replace("Inpectorate", "Inspectorate")       # typo on the DPSA page
        if provincial.match(name):
            employer = f"{name} Provincial Government"
        elif name == "Treasury":
            employer = "National Treasury"
        elif re.match(r"(the|south african)\b", name, re.I) or re.search(
                r"Inspectorate|Service\b|Services\b|Commission|Agency|Authority", name):
            employer = name
        else:
            employer = f"Department of {name}"
        count = (f"{posts} government vacanc{'y' if posts == 1 else 'ies'}" if posts
                 else "Government vacancies")
        body = [f"Public Service Vacancy Circular {num} of {year} - {employer}."]
        if titles:
            body.append("Posts include: " + "; ".join(titles[:8]) + ("; and more." if len(titles) > 8 else "."))
        if grade12:
            body.append("Some posts list Grade 12 / matric as a requirement.")
        body.append("Apply on the Z83 form to the department named in the advert, quoting the post reference number.")
        out.append({
            "employer": employer,
            "source_key": "dpsa",
            "source_name": "SA Government (DPSA Vacancy Circular)",
            "title": f"{employer}: {count} (Public Service Vacancy Circular {num} of {year})",
            "url": url,
            "published_raw": posted.group(1) if posted else "",
            "source_categories": ["vacancies"],
            "body": " ".join(body),
            "location_raw": name if provincial.match(name) else None,
            # Only a single date shared by every post in the section is trusted;
            # mixed dates stay unknown rather than picking one of them.
            "closing_raw": closes[0] if len(closes) == 1 else None,
            "allow_jobs": True,
        })
    err = None
    if not can_read:
        err = "pdftotext unavailable - listings kept without closing dates"
    elif errors:
        err = "unreadable PDFs: " + ", ".join(errors)
    return out, err


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

    log("  - SA Government (DPSA Vacancy Circular)")
    got, err = from_dpsa(log)
    if err:
        failures.append({"name": "SA Government (DPSA Vacancy Circular)", "reason": err})
    log(f"    {len(got)} listings")
    items.extend(got)
    return items, failures
