#!/usr/bin/env python3
"""Campus Plug — today's social posting plan from the opportunity fliers.

fliers.py renders a flier for every open listing; nobody can post 50 a day.
This picks the day's three "hero" listings and maps them onto platform slots
(SAST), each with the flier file to use, a teaser caption and a tracked link:

    07:30  WhatsApp Channel      roundup text + hero 1 (square)
    12:30  TikTok photo / Stories / Snapchat   hero 1 (story)
    13:00  X                     hero 2 (square)
    19:00  Instagram + Facebook  carousel of the heroes (portrait)
    20:30  X                     hero 3 (square)
    Mon    LinkedIn + IG/FB      "closing this week" carousel (portrait, up to 9)

Heroes: listings closing soonest that were not featured in the last
REPEAT_DAYS days, at most two from one category, matric-friendly and
logo'd fliers first among equals. state/social-featured.json remembers them.

Captions come from posts.teaser (the apply link is never in a caption). Links
are Play Install Referrer links (utm_campaign=opp_drop), one utm_source per
platform, so Play Console shows which platform installs came from.

    python3 social_plan.py              # plan for today's fliers
    python3 social_plan.py --dry-run    # print, write nothing, remember nothing
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

from email_digest import refresh_deadlines
from fliers import tracked_play_url
from posts import APP_NAME, BASE_TAGS, LIMITS, pretty_date, roundup, search_term, teaser

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state" / "social-featured.json"
HEROES = 3
REPEAT_DAYS = 5
WEEK = 7
CAROUSEL_MAX = 9

# Platforms where a caption link is clickable get one; the rest say "link in bio".
LINKED = {"x", "facebook", "whatsapp"}
BIO_LINE = f"📲 {APP_NAME} is free on Android — link in bio."


def with_link(text, platform):
    if platform not in LINKED:
        return f"{text}\n\n{BIO_LINE}" if platform != "x" else text
    link = tracked_play_url(platform, "social")
    if platform == "x":
        # X counts any link as 23 characters.
        if len(text) + 24 > LIMITS["x"]:
            text = "\n".join(l for l in text.split("\n") if not l.startswith("#"))
        if len(text) + 24 > LIMITS["x"]:
            return text
        return f"{text}\n{link}"
    return f"{text}\n\nGet {APP_NAME} (free, Android): {link}"


def pick_heroes(items, entries, featured, today):
    recent = {i for i, d in featured.items()
              if (today - date.fromisoformat(d)).days < REPEAT_DAYS}

    def rank(item):
        left = item.get("days_left")
        return (left if left is not None else 10**6,
                not item.get("matric_friendly"), not entries[item["id"]].get("logo"))

    pool = sorted((i for i in items if i["id"] in entries), key=rank)
    picks, per_cat = [], {}
    for fresh_only in (True, False):          # top up on a thin day: repeats, same category
        for item in pool:
            if len(picks) == HEROES:
                break
            cat = item.get("primary_category")
            if item in picks:
                continue
            if fresh_only and (item["id"] in recent or per_cat.get(cat, 0) >= 2):
                continue
            picks.append(item)
            per_cat[cat] = per_cat.get(cat, 0) + 1
    return picks


def slot(time, platforms, items, fmt, caption_platform, entries, caption=None, note=""):
    files = [entries[i["id"]]["files"][fmt] for i in items if fmt in entries[i["id"]]["files"]]
    if caption is None:
        caption = with_link(teaser(items[0], caption_platform)["text"], caption_platform)
    return {"time": time, "platforms": platforms, "format": fmt, "files": files,
            "listings": [i["id"] for i in items], "search_terms": [search_term(i) for i in items],
            "caption": caption, "note": note}


def carousel_caption(items, headline="closing soon"):
    """A carousel caption that only counts what is in the carousel."""
    lines = [f"📢 {len(items)} {'opportunity' if len(items) == 1 else 'opportunities'} {headline} — swipe 👉", ""]
    for i in items:
        close = pretty_date(i.get("closing_date"))
        lines.append(f"🔍 {search_term(i)} — {(i.get('primary_category') or 'opportunity')}"
                     + (f", closes {close}" if close else ""))
    lines += ["", f"Search the name on {APP_NAME} for the requirements and the apply link — "
              "we post the alert here, the details live in the app.", "",
              " ".join(BASE_TAGS + ["#Bursaries", "#Internships", "#Learnerships"])]
    return "\n".join(lines)


def build_plan(items, entries, featured, today, when):
    heroes = pick_heroes(items, entries, featured, today)
    if not heroes:
        return None, []
    h = heroes
    slots = [
        slot("07:30", ["WhatsApp Channel"], h[:1], "square", "whatsapp", entries,
             caption=with_link(roundup(items, "whatsapp", when)["text"], "whatsapp"),
             note="Post the roundup text, then the flier as a second message."),
        slot("12:30", ["TikTok (photo mode)", "Instagram Story", "Facebook Story", "Snapchat"],
             h[:1], "story", "tiktok", entries,
             note="TikTok: add a trending sound. Stories: add a link sticker to the Play link."),
        slot("19:00", ["Instagram feed", "Facebook Page"], heroes, "portrait", "instagram", entries,
             caption=with_link(carousel_caption(heroes), "instagram"),
             note="Carousel, one flier per slide. On Facebook the caption link is clickable — "
                  "swap the bio line for " + tracked_play_url("facebook", "social")),
    ]
    # X gets heroes 2 and 3 when there are that many; a thin day just has fewer slots.
    slots[2:2] = [slot("13:00", ["X"], h[1:2], "square", "x", entries)] if len(h) > 1 else []
    if len(h) > 2:
        slots.append(slot("20:30", ["X"], h[2:3], "square", "x", entries))
    if today.weekday() == 0:
        week = sorted((i for i in items if i["id"] in entries and i.get("days_left") is not None
                       and 0 <= i["days_left"] <= WEEK), key=lambda i: i["days_left"])[:CAROUSEL_MAX]
        if week:
            slots.append(slot("12:00", ["LinkedIn", "Instagram feed", "Facebook Page"], week,
                              "portrait", "linkedin", entries,
                              caption=with_link(carousel_caption(week, "closing this week"), "linkedin"),
                              note="Monday 'closing this week' carousel."))
    return heroes, slots


def markdown(plan):
    md = [f"# {APP_NAME} social plan — {plan['date']}", "",
          "Heroes: " + ", ".join(plan["search_terms"]), ""]
    for s in plan["slots"]:
        md += [f"## {s['time']} · {', '.join(s['platforms'])} ({s['format']})", "",
               *[f"- `{f}`" for f in s["files"]], "",
               *([f"_{s['note']}_", ""] if s["note"] else []),
               "```", s["caption"], "```", ""]
    return "\n".join(md)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--feed", default=str(ROOT / "out" / "latest.json"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    feed = json.loads(Path(args.feed).read_text(encoding="utf-8"))
    when = feed.get("collected_for", date.today().isoformat())
    items = refresh_deadlines(feed, print)
    folder = ROOT / "out" / f"fliers-{when}"
    try:
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print(f"no fliers for {when} - no social plan")
        return 0
    entries = {e["id"]: e for e in manifest.get("fliers", [])}
    featured = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    today = date.today()

    heroes, slots = build_plan(items, entries, featured, today, when)
    if not heroes:
        print("no open listings with fliers - no social plan")
        return 0
    plan = {"date": when, "heroes": [h["id"] for h in heroes],
            "search_terms": [search_term(h) for h in heroes], "slots": slots}
    text = markdown(plan)
    if args.dry_run:
        print(text)
        return 0

    (ROOT / "out" / f"social-plan-{when}.json").write_text(
        json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    (ROOT / "out" / f"social-plan-{when}.md").write_text(text, encoding="utf-8")
    featured.update({h["id"]: today.isoformat() for h in heroes})
    # Forget anything featured long ago, so the file stays small.
    featured = {i: d for i, d in featured.items() if (today - date.fromisoformat(d)).days < 60}
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(featured, indent=2), encoding="utf-8")
    print(f"social plan: {len(slots)} slots, heroes {', '.join(plan['search_terms'])}")
    return 0


if __name__ == "__main__":
    # The plan is a nice-to-have: a failure here must never stop the digest mail after it.
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"social plan FAILED: {exc!r}")
        sys.exit(0)
