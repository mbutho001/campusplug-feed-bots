"""Source adapters (Agent A's inputs). Each returns plain dicts; failures return [] and an error.

Only public RSS and public pages are used. Platforms without a permitted public feed are listed
in config.sources.unavailable and reported as such instead of being guessed at.
"""
import hashlib
import html
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from xml.etree import ElementTree

import requests

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
ATOM = "{http://www.w3.org/2005/Atom}"
GT_NS = {"ht": "https://trends.google.com/trending/rss"}
TRENDS24_RE = re.compile(r'<a href="https://twitter\.com/search\?q=[^"]*"[^>]*class=trend-link[^>]*>([^<]+)</a>')
SAST = timezone(timedelta(hours=2))
TRACKING = {"utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "ref", "fbclid", "gclid"}


def clean(text, limit=400):
    text = html.unescape(TAG_RE.sub(" ", html.unescape(text or "")))
    return WS_RE.sub(" ", text).strip()[:limit]


def canonical_url(url):
    parts = urlsplit(url.strip())
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if k.lower() not in TRACKING])
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), query, ""))


def url_id(url):
    return hashlib.sha1(canonical_url(url).encode()).hexdigest()


def parse_date(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        # "Saturday Oct 17 2026 19:21:59" (News24) and ISO forms
        for fmt in ("%A %b %d %Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
            try:
                dt = datetime.strptime(raw, fmt)
                break
            except ValueError:
                continue
        else:
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                return None
    if dt.tzinfo is None:  # News24 publishes SAST without an offset
        dt = dt.replace(tzinfo=SAST)
    return dt.astimezone(timezone.utc)


class Http:
    def __init__(self, cfg, log):
        self.s = requests.Session()
        self.ua = cfg["user_agent"]
        self.timeout = cfg["http_timeout"]
        self.log = log

    def get(self, url, retries=2, backoff=4, headers=None):
        hdrs = {"User-Agent": self.ua, "Accept-Language": "en-ZA,en;q=0.9"}
        hdrs.update(headers or {})
        err = None
        for attempt in range(retries + 1):
            try:
                r = self.s.get(url, headers=hdrs, timeout=self.timeout)
                if r.status_code == 200:
                    return r, None
                err = f"HTTP {r.status_code}"
                if r.status_code not in (429, 500, 502, 503, 504):
                    break
            except requests.RequestException as exc:
                err = type(exc).__name__
            if attempt < retries:
                time.sleep(backoff * (2 ** attempt))
        return None, err


def news_rss(http, feed):
    """RSS 2.0 or Atom -> items."""
    r, err = http.get(feed["url"])
    if r is None:
        return [], err
    try:
        root = ElementTree.fromstring(r.content)
    except ElementTree.ParseError as exc:
        return [], f"bad XML: {exc}"
    out = []
    entries = root.iter("item")
    for it in entries:
        title = clean(it.findtext("title"), 300)
        link = (it.findtext("link") or "").strip()
        if not title or not link.startswith("http"):
            continue
        src_name = feed["name"]
        if feed.get("aggregator"):
            # Google News titles end in " - Outlet"; keep the outlet as the real source
            m = re.match(r"^(.*) - ([^-]{2,60})$", title)
            if m:
                title, src_name = m.group(1).strip(), m.group(2).strip()
        out.append({
            "source_id": feed["id"], "source_name": src_name, "tier": feed["tier"], "kind": "news",
            "title": title, "summary": clean(it.findtext("description")), "url": link,
            "published_at": parse_date(it.findtext("pubDate")),
        })
    for e in root.iter(ATOM + "entry"):
        title = clean(e.findtext(ATOM + "title"), 300)
        link_el = e.find(ATOM + "link")
        link = link_el.get("href") if link_el is not None else ""
        if title and link:
            out.append({"source_id": feed["id"], "source_name": feed["name"], "tier": feed["tier"],
                        "kind": "news", "title": title, "summary": clean(e.findtext(ATOM + "summary")),
                        "url": link, "published_at": parse_date(e.findtext(ATOM + "updated"))})
    return out, None


def google_trends(http, conf):
    r, err = http.get(conf["url"])
    if r is None:
        return [], err
    try:
        root = ElementTree.fromstring(r.content)
    except ElementTree.ParseError as exc:
        return [], f"bad XML: {exc}"
    out = []
    for rank, it in enumerate(root.iter("item")):
        title = clean(it.findtext("title"), 120)
        if title:
            out.append({"platform": "Google Trends", "topic": title, "rank": rank,
                        "volume": (it.findtext("ht:approx_traffic", namespaces=GT_NS) or "").strip()})
    return out, None


def x_trends(http, conf):
    r, err = http.get(conf["url"])
    if r is None:
        return [], err
    seen, out = set(), []
    for raw in TRENDS24_RE.findall(r.text):  # first block = latest hourly snapshot
        name = html.unescape(raw).strip()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            out.append({"platform": "X (Twitter)", "topic": name, "rank": len(out), "volume": ""})
        if len(out) >= 40:
            break
    return out, None


def reddit(http, conf, log):
    """Text + link posts from SA subreddits via RSS (the .json API is 403 anonymously)."""
    out, errors = [], []
    for i, sub in enumerate(conf["subs"]):
        r, err = http.get(f"https://www.reddit.com/r/{sub}/hot.rss?limit=25", retries=4, backoff=6,
                          headers={"Accept": "application/atom+xml,application/xml"})
        if r is None:
            errors.append(f"r/{sub}: {err}")
        else:
            try:
                root = ElementTree.fromstring(r.content)
            except ElementTree.ParseError:
                root = None
                errors.append(f"r/{sub}: bad XML")
            for e in (root.findall(ATOM + "entry") if root is not None else []):
                title = clean(e.findtext(ATOM + "title"), 300)
                link_el = e.find(ATOM + "link")
                link = link_el.get("href") if link_el is not None else ""
                if title and link and "Daily Discussion" not in title:
                    out.append({"source_id": f"reddit_{sub.lower()}", "source_name": f"r/{sub}", "tier": 4,
                                "kind": "reddit", "title": title,
                                "summary": clean(e.findtext(ATOM + "content"), 300), "url": link,
                                "published_at": parse_date(e.findtext(ATOM + "updated"))})
        if i < len(conf["subs"]) - 1:
            time.sleep(conf.get("pace_seconds", 12))
    return out, "; ".join(errors) or None
