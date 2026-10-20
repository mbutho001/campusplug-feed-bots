"""Instagram + Facebook via the Meta Graph API.

Meta serves no public trending feed: hashtag search needs an Instagram Business
account token, and Page content needs a Page token. Both are read from
config.json. Without them the digest says the platform is unavailable instead
of scraping around the login wall.
"""
GRAPH = "https://graph.facebook.com/v21.0"


def fetch(fetcher, cfg, log):
    conf = cfg["platforms"]["meta"]
    if not conf.get("enabled"):
        return [], [], "disabled in config"
    token = (conf.get("access_token") or "").strip()
    if not token:
        log("  meta: no Graph API token configured -> skipped")
        return [], [], "no Graph API token (see README: Enabling Instagram/Facebook)"

    ig_items, fb_items = [], []
    ig_id = (conf.get("ig_business_id") or "").strip()
    if ig_id:
        for tag in cfg.get("meta_hashtags", ["studentlife", "southafrica", "campuslife"]):
            search = fetcher.get(f"{GRAPH}/ig_hashtag_search?user_id={ig_id}&q={tag}&access_token={token}",
                                 retries=1, backoff=3)
            if search is None:
                continue
            data = (search.json().get("data") or [])
            if not data:
                continue
            hid = data[0]["id"]
            top = fetcher.get(
                f"{GRAPH}/{hid}/top_media?user_id={ig_id}&fields=id,caption,media_type,media_url,permalink"
                f"&limit=10&access_token={token}", retries=1, backoff=3)
            if top is None:
                continue
            for rank, node in enumerate((top.json().get("data") or [])):
                if node.get("media_type") != "IMAGE":
                    continue  # skip VIDEO/CAROUSEL — data heavy
                ig_items.append({
                    "platform": "Instagram", "source": f"#{tag}", "kind": "image",
                    "tag": "instagram", "title": (node.get("caption") or f"#{tag} top post")[:180],
                    "permalink": node.get("permalink"), "image_url": node.get("media_url"),
                    "rank": rank, "weight": 1.2, "created": None,
                })
        log(f"  instagram: -> {len(ig_items)} image posts")

    page_id = (conf.get("fb_page_id") or "").strip()
    if page_id:
        posts = fetcher.get(f"{GRAPH}/{page_id}/posts?fields=message,permalink_url,full_picture,"
                            f"attachments{{media_type}}&limit=15&access_token={token}", retries=1, backoff=3)
        if posts is not None:
            for rank, node in enumerate((posts.json().get("data") or [])):
                pic, msg = node.get("full_picture"), (node.get("message") or "").strip()
                atts = ((node.get("attachments") or {}).get("data") or [{}])
                if atts and atts[0].get("media_type") == "video":
                    continue
                if not (pic and msg):
                    continue
                fb_items.append({
                    "platform": "Facebook", "source": "Page feed", "kind": "image",
                    "tag": "facebook", "title": msg[:180], "permalink": node.get("permalink_url"),
                    "image_url": pic, "rank": rank, "weight": 1.0, "created": None,
                })
            log(f"  facebook: -> {len(fb_items)} image posts")
    return ig_items, fb_items, None
