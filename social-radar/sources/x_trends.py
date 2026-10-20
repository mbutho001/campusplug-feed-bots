"""X / Twitter trending topics for South Africa (and worldwide) via trends24."""
import html
import re

from .common import BROWSER_UA

LINK_RE = re.compile(r'<a href="https://twitter\.com/search\?q=[^"]*"[^>]*class=trend-link[^>]*>([^<]+)</a>')


def _scrape(fetcher, url, scope, log):
    resp = fetcher.get(url, ua=BROWSER_UA, retries=2, backoff=4)
    if resp is None:
        log(f"  x/twitter: {scope} unavailable")
        return []
    names, seen = [], set()
    # The first block on the page is the most recent hourly snapshot.
    for raw in LINK_RE.findall(resp.text):
        name = html.unescape(raw).strip()
        key = name.lower()
        if name and key not in seen:
            seen.add(key)
            names.append(name)
    log(f"  x/twitter: {scope} -> {len(names)} trends")
    return [{"platform": "X (Twitter)", "source": scope, "topic": n, "rank": i,
             "url": "https://twitter.com/search?q=" + n.replace("#", "%23").replace(" ", "%20")}
            for i, n in enumerate(names[:40])]


def fetch(fetcher, cfg, log):
    out = _scrape(fetcher, "https://trends24.in/south-africa/", "South Africa", log)
    out += _scrape(fetcher, "https://trends24.in/", "Worldwide", log)
    return out
