#!/usr/bin/env python3
"""Campus Plug — upload past days' Shorts to YouTube (the days before the YT_* secrets existed).

The workflow keeps only today's and yesterday's out/social-<date>/ folders, so an older Short is
rebuilt as it was made that day: the day's own feed (out/opportunities-<date>.json), the day's
plan slot, and the clock set back to that date, so every countdown, the date kicker and the
voice-over read exactly as they did in the original. Each upload is recorded in
state/channel-posted.json as <date>#youtube, so the 17:00 slot never posts a day twice.

    python3 yt_backfill.py                                # every day of the last 7 not yet on YouTube
    python3 yt_backfill.py 2026-11-14 2026-11-15 ...      # these days, oldest first
    python3 yt_backfill.py --dry-run 2026-11-14

Stops at the first quotaExceeded (an upload costs a big share of the daily API quota); the
remaining days can be run again after the quota resets (09:00 SAST).
"""
import argparse
import datetime as dt
import json
import sys
from contextlib import contextmanager
from pathlib import Path

import channel_post
import email_digest
import fliers
import shorts

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
STATE = ROOT / "state" / "channel-posted.json"


@contextmanager
def clock(day):
    """Make date.today() return `day` inside the modules that compute countdowns."""
    fixed = dt.date.fromisoformat(day)

    class Day(dt.date):
        @classmethod
        def today(cls):
            return fixed

    saved = {m: m.date for m in (email_digest, fliers, shorts)}
    for m in saved:
        m.date = Day
    try:
        yield
    finally:
        for m, d in saved.items():
            m.date = d


def youtube_slot(day):
    plan = json.loads((OUT / f"social-plan-{day}.json").read_text(encoding="utf-8"))
    return next(s for s in plan["slots"] if "YouTube Shorts" in s["platforms"])


def rebuild(day, slot):
    video = OUT / f"social-{day}" / f"short-{day}.mp4"
    if video.exists():
        return video
    folder = OUT / f"social-{day}"
    folder.mkdir(parents=True, exist_ok=True)
    feed = OUT / f"opportunities-{day}.json"
    with clock(day):
        for lid in slot["listings"]:
            argv = sys.argv
            sys.argv = ["fliers.py", "--feed", str(feed), "--only", lid, "--formats", "story",
                        "--out-dir", str(folder)]
            try:
                fliers.main()
            finally:
                sys.argv = argv
        # shorts.build reads out/latest.json for the listing facts: point it at that day's feed.
        latest = OUT / "latest.json"
        saved = latest.read_bytes() if latest.exists() else None
        latest.write_bytes(feed.read_bytes())
        try:
            return shorts.build(day)
        finally:
            if saved is not None:
                latest.write_bytes(saved)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("days", nargs="*")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    posted = json.loads(STATE.read_text()) if STATE.exists() else {}
    days = args.days
    if not days:
        today = dt.datetime.now(channel_post.SAST).date()
        days = [d for d in ((today - dt.timedelta(days=n)).isoformat() for n in range(7, 0, -1))
                if (OUT / f"social-plan-{d}.json").exists()
                and "id" not in posted.get(f"{d}#youtube", {})]
        print("days to backfill:", ", ".join(days) or "none")
    failures = 0
    for day in days:
        key = f"{day}#youtube"
        if key in posted and "error" not in posted[key]:
            print(f"{key}: already posted {posted[key]['id']}")
            continue
        try:
            slot = youtube_slot(day)
            video = rebuild(day, slot)
            if not video or not video.exists():
                print(f"{key}: Short could not be rebuilt - skipped")
                failures += 1
                continue
            if args.dry_run:
                print(f"[dry-run] {key}: {video} ({video.stat().st_size} bytes)\n{slot['title']}")
                continue
            vid, note = channel_post.youtube_post(slot, video)
            if vid is None:
                print(f"{key}: {note}")
                return 1
            posted[key] = {"id": vid, "at": dt.datetime.now(channel_post.SAST).isoformat(timespec="minutes"),
                           "backfill": True}
            STATE.write_text(json.dumps(posted, indent=2))
            print(f"{key}: posted {note}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"{key}: FAILED {exc}")
            if "quota" in str(exc).lower():
                print("YouTube quota used up - run the remaining days after 09:00 SAST")
                break
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
