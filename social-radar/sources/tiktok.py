"""TikTok trending hashtags via the Creative Center.

Anonymous requests are rejected (code 40101 "no permission"). If the operator
pastes a Creative Center browser cookie into config.json the live endpoint is
used; otherwise the platform is reported as unavailable rather than guessed at.
"""
from .common import BROWSER_UA

API = ("https://ads.tiktok.com/creative_radar_api/v1/popular_trend/hashtag/list"
       "?period=7&page=1&limit=20&country_code={region}&sort_by=popular")


def fetch(fetcher, cfg, log):
    conf = cfg["platforms"]["tiktok"]
    if not conf.get("enabled"):
        return [], "disabled in config"
    cookie = (conf.get("creative_center_cookie") or "").strip()
    if not cookie:
        log("  tiktok: no Creative Center cookie configured -> skipped")
        return [], "no cookie configured (see README: Enabling TikTok)"

    resp = fetcher.get(API.format(region=conf.get("region", "ZA")), ua=BROWSER_UA,
                       headers={"Cookie": cookie, "Referer": "https://ads.tiktok.com/business/creativecenter/"},
                       retries=1, backoff=4)
    if resp is None:
        return [], "endpoint unreachable"
    try:
        payload = resp.json()
    except ValueError:
        return [], "unreadable response"
    if payload.get("code") != 0:
        msg = payload.get("msg", "rejected")
        log(f"  tiktok: {msg}")
        return [], f"cookie rejected ({msg}) — refresh it"

    rows = (payload.get("data") or {}).get("list") or []
    out = []
    for rank, row in enumerate(rows):
        name = row.get("hashtag_name") or ""
        if not name:
            continue
        out.append({
            "platform": "TikTok",
            "source": conf.get("region", "ZA"),
            "topic": "#" + name.lstrip("#"),
            "rank": rank,
            "volume": str(row.get("publish_cnt") or ""),
            "url": f"https://www.tiktok.com/tag/{name.lstrip('#')}",
        })
    log(f"  tiktok: -> {len(out)} hashtags")
    return out, None
