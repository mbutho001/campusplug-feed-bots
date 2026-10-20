"""Google daily search trends for South Africa (public RSS)."""
from xml.etree import ElementTree

from .common import BROWSER_UA

NS = {"ht": "https://trends.google.com/trending/rss"}


def fetch(fetcher, cfg, log):
    url = "https://trends.google.com/trending/rss?geo=ZA"
    resp = fetcher.get(url, ua=BROWSER_UA, retries=2, backoff=4)
    if resp is None:
        log("  google trends: unavailable")
        return []
    try:
        root = ElementTree.fromstring(resp.content)
    except ElementTree.ParseError as exc:
        log(f"  google trends: bad XML ({exc})")
        return []
    out = []
    for rank, item in enumerate(root.iter("item")):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        traffic = (item.findtext("ht:approx_traffic", namespaces=NS) or "").strip()
        picture = (item.findtext("ht:picture", namespaces=NS) or "").strip()
        out.append({
            "platform": "Google Trends",
            "source": "South Africa",
            "topic": title,
            "rank": rank,
            "volume": traffic,
            "image_url": picture or None,
            "url": "https://www.google.com/search?q=" + title.replace(" ", "+"),
        })
    log(f"  google trends: -> {len(out)} trends")
    return out
