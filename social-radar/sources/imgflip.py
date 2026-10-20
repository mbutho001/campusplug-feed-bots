"""Imgflip's most-used meme templates — the blank canvases to caption."""


def fetch(fetcher, cfg, log):
    resp = fetcher.get("https://api.imgflip.com/get_memes", retries=1, backoff=3)
    if resp is None:
        log("  imgflip: unavailable")
        return []
    try:
        memes = (resp.json().get("data") or {}).get("memes") or []
    except ValueError:
        return []
    out = []
    for rank, m in enumerate(memes[:12]):
        if m.get("box_count", 0) > 4:
            continue
        out.append({"name": m.get("name"), "url": m.get("url"),
                    "page": f"https://imgflip.com/memetemplate/{m.get('id')}", "rank": rank})
    log(f"  imgflip: -> {len(out)} templates")
    return out
