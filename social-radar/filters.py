"""Content gates: no video, nothing unsafe, nothing off-brand."""
from urllib.parse import urlparse


def is_video(url, cfg):
    """True for anything that would pull down a heavy clip."""
    if not url:
        return True
    low = url.lower()
    if any(low.endswith(ext) or ext + "?" in low for ext in cfg["video_extensions"]):
        return True
    return any(dom in low for dom in cfg["video_blocklist"])


def is_safe(text, cfg):
    low = (text or "").lower()
    return not any(word in low for word in cfg["banned_words"])


def keyword_hits(text, words):
    low = (text or "").lower()
    return [w.strip() for w in words if w in low]


def score(item, cfg):
    """Rank a candidate: feed weight, position in feed, and local relevance."""
    base = 10.0 * float(item.get("weight", 1.0))
    base -= 0.25 * int(item.get("rank", 0))          # earlier in the feed = hotter
    text = f"{item.get('title','')} {item.get('source','')}"
    student = keyword_hits(text, cfg["student_keywords"])
    sa = keyword_hits(text, cfg["sa_keywords"])
    base += 3.0 * len(student) + 2.0 * len(sa)
    item["matched"] = sorted(set(student + sa))
    return round(base, 2)


def host(url):
    try:
        return urlparse(url).netloc
    except ValueError:
        return ""
