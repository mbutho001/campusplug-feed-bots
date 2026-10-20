"""Reddit adapter.

Reddit blocks the .json endpoints for unauthenticated clients but still serves
per-subreddit RSS, which carries the post title, permalink and — for image
posts — the direct i.redd.it URL. Rate limiting is aggressive, so feeds are
fetched slowly and only a rotating slice is pulled on each run.
"""
import html
import re
import time
from datetime import datetime, timezone
from xml.etree import ElementTree

ATOM = "{http://www.w3.org/2005/Atom}"
IMG_RE = re.compile(r'https://(?:i\.redd\.it|preview\.redd\.it)/[^\s"\'<>]+')


def _pick_image(content_html):
    """Prefer the full-res i.redd.it asset; fall back to the widest preview."""
    urls = [html.unescape(u) for u in IMG_RE.findall(content_html or "")]
    direct = [u for u in urls if u.startswith("https://i.redd.it/")]
    if direct:
        return direct[0]
    previews = [u for u in urls if "preview.redd.it" in u]
    return previews[0] if previews else None


def _parse_ts(node):
    raw = (node.findtext(ATOM + "updated") or node.findtext(ATOM + "published") or "")
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)


def fetch(fetcher, cfg, feeds, log):
    """Return raw candidate items from the given feed definitions."""
    run = cfg["run"]
    items = []
    for idx, feed in enumerate(feeds):
        sub, sort = feed["sub"], feed["sort"]
        # "hot" -> hot.rss?limit=25 ; "top?t=day" -> top.rss?t=day&limit=25
        path, _, query = sort.partition("?")
        query = (query + "&" if query else "") + "limit=25"
        url = f"https://www.reddit.com/r/{sub}/{path}.rss?{query}"
        resp = fetcher.get(url, retries=run["reddit_max_retries"], backoff=5,
                           headers={"Accept": "application/atom+xml,application/xml"})
        if resp is None:
            log(f"  reddit: r/{sub} unavailable")
        else:
            try:
                root = ElementTree.fromstring(resp.content)
            except ElementTree.ParseError as exc:
                log(f"  reddit: r/{sub} bad XML ({exc})")
                root = None
            if root is not None:
                found = 0
                for rank, entry in enumerate(root.findall(ATOM + "entry")):
                    title = (entry.findtext(ATOM + "title") or "").strip()
                    link_el = entry.find(ATOM + "link")
                    permalink = link_el.get("href") if link_el is not None else None
                    content = entry.findtext(ATOM + "content") or ""
                    image = _pick_image(content)
                    if not (title and permalink and image):
                        continue
                    items.append({
                        "platform": "Reddit",
                        "source": f"r/{sub}",
                        "kind": "image",
                        "tag": feed["tag"],
                        "title": title,
                        "permalink": permalink,
                        "image_url": image,
                        "rank": rank,
                        "weight": feed["weight"],
                        "created": _parse_ts(entry),
                    })
                    found += 1
                log(f"  reddit: r/{sub} -> {found} image posts")
        if idx < len(feeds) - 1:
            time.sleep(run["reddit_pace_seconds"])
    return items
