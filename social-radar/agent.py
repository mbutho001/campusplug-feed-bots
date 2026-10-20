#!/usr/bin/env python3
"""Campus Plug Social Radar.

Scans Reddit, X/Twitter, Google Trends, TikTok, Instagram and Facebook for
trending topics and image-based jokes/memes worth posting on the Campus Plug
social page, then emails a compact digest. Video is excluded by design and
every image is re-encoded small, so the daily mail stays light.

    python3 agent.py                 # scan and email
    python3 agent.py --dry-run       # scan, write preview HTML, send nothing
    python3 agent.py --no-images     # topics only (fastest smoke test)
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import filters
import media
import render
import send as sender
import store
from sources import common, google_trends, imgflip, meta, reddit, tiktok, x_trends

ROOT = Path(__file__).resolve().parent
SAST = ZoneInfo("Africa/Johannesburg")


def make_logger(path):
    handle = open(path, "a", encoding="utf-8")

    def log(msg):
        line = f"{datetime.now(SAST):%Y-%m-%d %H:%M:%S} {msg}"
        print(line, flush=True)
        handle.write(line + "\n")
        handle.flush()
    return log


def rotate_feeds(cfg, con):
    """Pull a different slice of subreddits each run so content keeps turning over."""
    feeds = cfg["reddit_feeds"]
    n = min(cfg["run"]["feeds_per_run"], len(feeds))
    runs = con.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    start = (runs * n) % len(feeds)
    return [feeds[(start + i) % len(feeds)] for i in range(n)]


def collect_topics(fetcher, cfg, log, unavailable):
    topics = []
    topics += x_trends.fetch(fetcher, cfg, log)
    topics += google_trends.fetch(fetcher, cfg, log)
    tt_rows, tt_note = tiktok.fetch(fetcher, cfg, log)
    topics += tt_rows
    if tt_note:
        unavailable["TikTok"] = tt_note
    return topics


def rank_topics(topics, cfg, con, limit):
    """Boost topics that overlap platforms or read student/SA, drop repeats."""
    counts = {}
    for t in topics:
        counts[t["topic"].lower().lstrip("#")] = counts.get(t["topic"].lower().lstrip("#"), 0) + 1
    scored = []
    for t in topics:
        if not filters.is_safe(t["topic"], cfg):
            continue
        key = t["topic"].lower().lstrip("#")
        s = 20.0 - 0.4 * t["rank"] + 6.0 * (counts[key] - 1)
        s += 4.0 * len(filters.keyword_hits(t["topic"], cfg["student_keywords"]))
        s += 3.0 * len(filters.keyword_hits(t["topic"], cfg["sa_keywords"]))
        if store.is_new(con, store.item_id(t["platform"], key)):
            s += 5.0                      # unseen beats something already mailed
        t["score"] = s
        scored.append(t)
    scored.sort(key=lambda x: -x["score"])
    seen, out = set(), []
    for t in scored:
        key = (t["platform"], t["topic"].lower().lstrip("#"))
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
        if len(out) >= limit:
            break
    return out


def main():
    ap = argparse.ArgumentParser(description="Campus Plug social media trend + meme digest")
    ap.add_argument("--dry-run", action="store_true", help="scan and preview, send no mail")
    ap.add_argument("--no-images", action="store_true", help="skip Reddit/Meta image harvesting")
    ap.add_argument("--config", default=str(ROOT / "config.json"))
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).read_text())
    log = make_logger(ROOT / "logs" / "agent.log")
    stamp = datetime.now(SAST)
    log(f"=== run start ({'dry-run' if args.dry_run else 'live'}) ===")

    con = store.connect()
    fetcher = common.Fetcher(cfg, log)
    unavailable = {}

    topics = rank_topics(collect_topics(fetcher, cfg, log, unavailable), cfg, con,
                         cfg["run"]["max_topics_per_email"])

    candidates = []
    if not args.no_images:
        candidates += reddit.fetch(fetcher, cfg, rotate_feeds(cfg, con), log)
        ig, fb, meta_note = meta.fetch(fetcher, cfg, log)
        candidates += ig + fb
        if meta_note:
            unavailable["Instagram"] = meta_note
            unavailable["Facebook"] = meta_note
    else:
        unavailable["Reddit"] = "skipped (--no-images)"

    # Gate: no video, nothing unsafe, nothing already mailed.
    fresh = []
    for item in candidates:
        if filters.is_video(item["image_url"], cfg) or filters.is_video(item["permalink"], cfg):
            continue
        if not filters.is_safe(item["title"], cfg):
            continue
        iid = store.item_id(item["platform"], item["permalink"])
        if not store.is_new(con, iid):
            continue
        item["id"] = iid
        item["score"] = filters.score(item, cfg)
        fresh.append(item)
    fresh.sort(key=lambda x: -x["score"])
    log(f"  candidates: {len(candidates)} raw -> {len(fresh)} fresh, non-video")

    picks, images, total = [], [], 0
    budget = cfg["media"]["max_total_attachment_bytes"]
    for item in fresh:
        if len(picks) >= cfg["run"]["max_items_per_email"]:
            break
        got = media.grab(fetcher, item["image_url"], cfg, log)
        if not got:
            continue
        blob, w, h = got
        if total + len(blob) > budget:
            log("  media: attachment budget reached")
            break
        cid = sender.new_cid()
        item.update(cid=cid, w=min(w, 600), h=h, kb=round(len(blob) / 1024))
        picks.append(item)
        images.append((cid, blob))
        total += len(blob)
    log(f"  packed {len(picks)} images, {round(total/1024)} KB total")

    templates = imgflip.fetch(fetcher, cfg, log) if picks or topics else []

    if not picks and not topics:
        log("  nothing new to report — no mail sent")
        store.record_run(con, 0, "empty")
        store.prune(con)
        con.commit()
        return 0

    html_body, text_body = render.build(picks, topics, templates, unavailable, stamp, cfg)
    subject = render.subject(cfg, picks, topics, stamp)

    if args.dry_run:
        preview = ROOT / "logs" / "preview.html"
        preview.write_text(html_body, encoding="utf-8")
        for cid, blob in images:                     # inline the images so the preview renders
            import base64
            html_body = html_body.replace(
                f"cid:{cid}", "data:image/jpeg;base64," + base64.b64encode(blob).decode())
        preview.write_text(html_body, encoding="utf-8")
        log(f"  dry-run: {subject}")
        log(f"  preview -> {preview}")
        store.record_run(con, 0, "dry-run")
        store.prune(con)
        con.commit()
        return 0

    ok = sender.send(cfg, subject, html_body, text_body, images, log)
    if ok:
        for item in picks:
            store.mark(con, item["id"], item["platform"], item["title"])
        for t in topics:
            store.mark(con, store.item_id(t["platform"], t["topic"].lower().lstrip("#")),
                       t["platform"], t["topic"])
        store.record_run(con, len(picks), "sent")
    else:
        store.record_run(con, 0, "smtp-failed")
    store.prune(con)
    con.commit()
    log(f"=== run end ({'sent' if ok else 'FAILED'}) ===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
