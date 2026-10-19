"""Reads source articles in Python so the free models get the actual article text as evidence
(they replace claude -p's WebFetch). Blocked or empty pages fall back to the outlet's RSS summary."""
import html
import re

from .sources import Http, clean

SCRIPT_RE = re.compile(r"<(script|style|noscript|svg|nav|footer|header|aside|form)\b.*?</\1>", re.S | re.I)
P_RE = re.compile(r"<p\b[^>]*>(.*?)</p>", re.S | re.I)
META_RE = re.compile(r'<meta[^>]+(?:property|name)=["\'](og:title|og:description|article:published_time|'
                     r'description)["\'][^>]*content=["\']([^"\']*)["\']', re.I)
BOILER = re.compile(r"cookie|subscribe|newsletter|sign up|all rights reserved|follow us|download our app|"
                    r"advertis|javascript|whatsapp channel", re.I)


def extract(page, limit):
    meta = {k.lower(): html.unescape(v) for k, v in META_RE.findall(page)}
    body = SCRIPT_RE.sub(" ", page)
    paras = [clean(p, 2000) for p in P_RE.findall(body)]
    paras = [p for p in paras if len(p) > 60 and not BOILER.search(p)]
    text = " ".join(paras)[:limit]
    return {"title": meta.get("og:title", ""), "description": meta.get("og:description") or meta.get("description", ""),
            "published": meta.get("article:published_time", ""), "text": text}


def evidence(cfg, log, sources, max_sources=3, per_source=2500):
    """Returns a list of {outlet, url, fetched, title, published, text} for the first direct sources."""
    http = Http(cfg, log)
    out = []
    for s in sources:
        if len(out) >= max_sources:
            break
        url = s.get("url") or ""
        if not url or "news.google.com" in url:
            continue
        r, err = http.get(url, retries=1, backoff=2)
        got = extract(r.text, per_source) if r is not None else None
        if got and len(got["text"]) >= 300:
            out.append({"outlet": s.get("outlet"), "url": url, "fetched": True, "title": got["title"] or s.get("title"),
                        "published": got["published"] or s.get("published_at"), "text": got["text"]})
        else:
            out.append({"outlet": s.get("outlet"), "url": url, "fetched": False, "title": s.get("title"),
                        "published": s.get("published_at"),
                        "text": f"(page not readable: {err or 'no article text'}) RSS summary: {s.get('summary', '')}"})
    return out


def news_search(cfg, log, query, limit=8):
    """Free web corroboration for models without a search tool: Google News ZA search RSS headlines."""
    from urllib.parse import quote_plus
    from .sources import news_rss
    feed = {"id": "gnews_search", "name": "Google News search", "tier": 2, "aggregator": True,
            "url": f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-ZA&gl=ZA&ceid=ZA:en"}
    items, err = news_rss(Http(cfg, log), feed)
    if err:
        log(f"news_search: {err}")
    return [{"outlet": i["source_name"], "title": i["title"], "published": str(i["published_at"] or "")[:16]} for i in items[:limit]]
