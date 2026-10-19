"""HTTP + lenient feed/HTML parsing shared by every source adapter.

Several of the SA opportunity sites emit RSS that is not well-formed XML
(duplicate attributes, stray bytes before the declaration). ElementTree
refuses all of it, so feeds are parsed with a tolerant regex reader instead.
"""
import gzip
import html as _html
import io
import re
import time
import urllib.error
import urllib.request
import zlib

BROWSER_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")


def get(url, log=print, retries=2, timeout=30, headers=None):
    """GET a URL, returning decoded text or None. Never raises."""
    hdrs = {
        "User-Agent": BROWSER_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-ZA,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
    }
    if headers:
        hdrs.update(headers)
    delay = 3
    for attempt in range(1, retries + 2):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                enc = (resp.headers.get("Content-Encoding") or "").lower()
            if enc == "gzip":
                raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
            elif enc == "deflate":
                raw = zlib.decompress(raw, -zlib.MAX_WBITS)
            return raw.decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            # 403/404 are verdicts, not hiccups - stop immediately.
            if exc.code not in (429, 500, 502, 503, 504):
                log(f"    ! {url} -> HTTP {exc.code}")
                return None
            log(f"    . {url} -> HTTP {exc.code}, retry {attempt}")
        except Exception as exc:  # noqa: BLE001 - a dead source must not kill the run
            log(f"    ! {url} attempt {attempt}: {exc}")
        if attempt <= retries:
            time.sleep(delay)
            delay *= 2
    return None


# ---------------------------------------------------------------- feed parsing

_ITEM_RE = re.compile(r"<item[\s>].*?</item>", re.S | re.I)


def _tag(block, name):
    m = re.search(rf"<{name}(?:\s[^>]*)?>(.*?)</{name}>", block, re.S | re.I)
    return unwrap(m.group(1)) if m else ""


def unwrap(text):
    """Strip CDATA wrappers and decode entities."""
    text = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", text, flags=re.S)
    return _html.unescape(text).strip()


def strip_tags(text):
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text or "")
    text = re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = _html.unescape(text)
    return re.sub(r"[ \t\xa0]+", " ", text).strip()


def parse_feed(xml_text):
    """Tolerant RSS/Atom reader -> list of dicts. Works on malformed XML."""
    if not xml_text:
        return []
    items = []
    for block in _ITEM_RE.findall(xml_text):
        body = _tag(block, "content:encoded") or _tag(block, "description")
        items.append({
            "title": strip_tags(_tag(block, "title")),
            "link": _tag(block, "link"),
            "published": _tag(block, "pubDate") or _tag(block, "dc:date"),
            "categories": [unwrap(c) for c in
                           re.findall(r"<category(?:\s[^>]*)?>(.*?)</category>", block, re.S | re.I)],
            "body": strip_tags(body),
        })
    # Atom fallback, for any source that switches format on us.
    if not items:
        for block in re.findall(r"<entry[\s>].*?</entry>", xml_text, re.S | re.I):
            link = re.search(r'<link[^>]*href="([^"]+)"', block, re.I)
            items.append({
                "title": strip_tags(_tag(block, "title")),
                "link": link.group(1) if link else "",
                "published": _tag(block, "updated") or _tag(block, "published"),
                "categories": re.findall(r'<category[^>]*term="([^"]+)"', block, re.I),
                "body": strip_tags(_tag(block, "content") or _tag(block, "summary")),
            })
    return items
