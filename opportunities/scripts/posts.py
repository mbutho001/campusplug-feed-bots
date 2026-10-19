#!/usr/bin/env python3
"""Campus Plug — turn a collected opportunity feed into publishable posts.

Two very different outputs, on purpose:

  app posts     full detail: employer, closing date, province, apply link.
                This is what goes into the Campus Plug app.

  social teasers  detail deliberately withheld: the hook, the deadline
                pressure, and a search term - never the apply link. The point
                is to move someone from Instagram/X/TikTok/WhatsApp into the
                app to get the rest.

    python3 posts.py                        # read out/latest.json
    python3 posts.py --feed out/opportunities-2026-09-14.json
    python3 posts.py --top 8 --platforms instagram,x,whatsapp

Every number in a caption is counted from the feed and every fact is copied
from a collected field. Nothing is written that the sources did not say.
"""
import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_NAME = "Campus Plug"

LIMITS = {"x": 280, "instagram": 2200, "facebook": 2000,
          "tiktok": 2200, "whatsapp": 1000, "linkedin": 3000}

CATEGORY_EMOJI = {
    "bursary": "🎓", "internship": "💼", "learnership": "🛠️",
    "apprenticeship": "🔧", "in-service training": "📋",
    "graduate programme": "🚀",
}

BASE_TAGS = ["#CampusPlug", "#SAJobs", "#Mzansi"]
CATEGORY_TAGS = {
    "bursary": ["#Bursaries2027", "#BursarySA", "#StudentFunding"],
    "internship": ["#Internships", "#InternshipSA", "#GraduateJobs"],
    "learnership": ["#Learnerships", "#LearnershipSA", "#YouthEmployment"],
    "apprenticeship": ["#Apprenticeships", "#Artisan", "#SkillsSA"],
    "in-service training": ["#InServiceTraining", "#WIL", "#P1P2"],
    "graduate programme": ["#GraduateProgramme", "#GradJobs", "#Careers"],
}


# --------------------------------------------------------------- search terms

_NOISE = re.compile(r"\b(programme|program|the|of|and|for|south|africa|sa|20\d\d|"
                    r"\d+|posts?|month|r\d[\d,]*)\b", re.I)


def search_term(item):
    """The phrase a student should type into Campus Plug to find this listing.

    Prefers the employer, because that is what someone remembers from a
    screenshot two hours later.
    """
    if item.get("employer"):
        return item["employer"]
    words = _NOISE.sub(" ", item["title"]).split()
    return " ".join(words[:3]) if words else item.get("primary_category", "opportunities")


def urgency(item):
    left = item.get("days_left")
    if left is None:
        return None
    if left <= 0:
        return "CLOSES TODAY"
    if left == 1:
        return "CLOSES TOMORROW"
    if left <= 7:
        return f"{left} DAYS LEFT"
    if left <= 14:
        return f"Closes in {left} days"
    return None


def pretty_date(iso):
    if not iso:
        return None
    d = date.fromisoformat(iso)
    return d.strftime("%-d %B %Y")


# ----------------------------------------------------------------- app output

def app_post(item):
    """A full, detail-complete record for publishing inside the app."""
    bits = []
    if item.get("employer"):
        bits.append(f"**Employer:** {item['employer']}")
    if item.get("primary_category"):
        bits.append(f"**Type:** {item['primary_category'].title()}")
    if item.get("closing_date"):
        left = item.get("days_left")
        tail = f" ({left} days left)" if left is not None and left >= 0 else ""
        bits.append(f"**Closing date:** {pretty_date(item['closing_date'])}{tail}")
    else:
        bits.append("**Closing date:** not stated on the source listing — check before applying")
    if item.get("province"):
        bits.append(f"**Province:** {item['province']}")
    if item.get("location"):
        bits.append(f"**Location:** {item['location']}")
    if item.get("fields"):
        bits.append(f"**Fields:** {', '.join(item['fields'])}")
    if item.get("matric_friendly"):
        bits.append("**Open to Grade 12 / matric applicants**")

    body = "\n".join(bits)
    if item.get("summary"):
        body += f"\n\n{item['summary']}"
    body += f"\n\n**Apply / full details:** {item['url']}\n_Listed via {item['source']}_"

    return {
        "id": item["id"],
        "title": item["title"],
        "category": item.get("primary_category"),
        "categories": item.get("categories", []),
        "employer": item.get("employer"),
        "province": item.get("province"),
        "fields": item.get("fields", []),
        "closing_date": item.get("closing_date"),
        "days_left": item.get("days_left"),
        "matric_friendly": item.get("matric_friendly", False),
        "apply_url": item["url"],
        "source": item["source"],
        "search_term": search_term(item),
        "body_markdown": body,
    }


# -------------------------------------------------------------- social output

def _tags(item, limit=6):
    tags = BASE_TAGS + CATEGORY_TAGS.get(item.get("primary_category"), [])
    if item.get("province"):
        tags.append("#" + item["province"].replace(" ", ""))
    if item.get("matric_friendly"):
        tags.append("#Matric2026")
    seen, out = set(), []
    for t in tags:
        if t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out[:limit]


def _hook(item):
    emoji = CATEGORY_EMOJI.get(item.get("primary_category"), "📢")
    cat = (item.get("primary_category") or "opportunity").title()
    who = item.get("employer")
    return f"{emoji} {who} {cat.lower()} is open" if who else f"{emoji} New {cat.lower()} is open"


def teaser(item, platform):
    """A caption that creates the pull but withholds the payoff.

    The apply link never appears here - that is the whole mechanism. What the
    reader gets is the deadline and the search term.
    """
    term = search_term(item)
    urge = urgency(item)
    close = pretty_date(item.get("closing_date"))
    cta = f'Search "{term}" on {APP_NAME} for the full details and the apply link.'
    tags = " ".join(_tags(item, 4 if platform == "x" else 6))

    if platform == "x":
        head = _hook(item)
        line = f"{urge} — closes {close}." if urge and close else \
               (f"Closes {close}." if close else "Applications are open now.")
        short = f'{head}\n{line}\nSearch "{term}" on {APP_NAME}. 📲'
        text = f"{short}\n{' '.join(_tags(item, 3))}"
        if len(text) > LIMITS["x"]:
            text = short[:LIMITS["x"] - 14].rstrip() + "\n#CampusPlug"

    elif platform == "whatsapp":
        lines = [_hook(item).upper()]
        if urge:
            lines.append(f"⏳ {urge}")
        if close:
            lines.append(f"Closing date: {close}")
        if item.get("province"):
            lines.append(f"📍 {item['province']}")
        if item.get("matric_friendly"):
            lines.append("✅ Matric is enough to apply")
        lines += ["", cta, "", f"Get {APP_NAME} and never miss a deadline again."]
        text = "\n".join(lines)

    else:  # instagram, facebook, tiktok, linkedin
        lines = [_hook(item)]
        if urge:
            lines.append(f"⏳ {urge}")
        lines.append("")
        if close:
            lines.append(f"📅 Closing date: {close}")
        if item.get("province"):
            lines.append(f"📍 {item['province']}")
        if item.get("fields"):
            lines.append(f"📚 {', '.join(item['fields'][:3])}")
        if item.get("matric_friendly"):
            lines.append("✅ Open to matric applicants")
        lines += ["", "We're not putting the link here 👀", cta, "",
                  f"New opportunities go up on {APP_NAME} every day.", "", tags]
        text = "\n".join(lines)

    limit = LIMITS.get(platform, 2000)
    return {"platform": platform, "search_term": term,
            "characters": len(text), "within_limit": len(text) <= limit,
            "text": text[:limit]}


_PLURALS = {"bursary": "bursaries", "internship": "internships",
            "learnership": "learnerships", "apprenticeship": "apprenticeships",
            "in-service training": "in-service training placements",
            "graduate programme": "graduate programmes"}


def plural(category, n):
    if n == 1:
        return category
    return _PLURALS.get(category, category + "s")


def roundup(items, platform, when):
    """One post covering the whole drop - every figure counted, none invented."""
    total = len(items)
    closing_week = [i for i in items if i.get("days_left") is not None and 0 <= i["days_left"] <= 7]
    counts = {}
    for i in items:
        c = i.get("primary_category")
        if c:
            counts[c] = counts.get(c, 0) + 1
    breakdown = " · ".join(f"{n} {plural(c, n)}"
                           for c, n in sorted(counts.items(), key=lambda kv: -kv[1]))
    terms = [search_term(i) for i in items[:4]]

    if platform == "x":
        text = (f"📢 {total} new opportunities on {APP_NAME} today.\n"
                f"{breakdown}\n"
                + (f"{len(closing_week)} close within 7 days ⏳\n" if closing_week else "")
                + f"Open {APP_NAME} and search. 📲\n#CampusPlug #SAJobs")
        if len(text) > LIMITS["x"]:
            text = (f"📢 {total} new opportunities on {APP_NAME} today.\n"
                    + (f"{len(closing_week)} close within 7 days ⏳\n" if closing_week else "")
                    + f"Open the app and search. 📲\n#CampusPlug #SAJobs")
    elif platform == "whatsapp":
        text = "\n".join([
            f"📢 {APP_NAME} — {when} opportunity drop",
            f"{total} new openings: {breakdown}.",
            *( [f"⏳ {len(closing_week)} of them close within 7 days."] if closing_week else [] ),
            "",
            "Start with:",
            *[f"• {t}" for t in terms],
            "",
            f"Search those names in {APP_NAME} for closing dates and apply links.",
        ])
    else:
        text = "\n".join([
            f"📢 {total} NEW OPPORTUNITIES ON {APP_NAME.upper()} TODAY",
            "",
            breakdown + ".",
            *( [f"⏳ {len(closing_week)} close within the next 7 days."] if closing_week else [] ),
            "",
            "A few to search for 👇",
            *[f"🔍 {t}" for t in terms],
            "",
            f"Closing dates and apply links are all in {APP_NAME} — "
            "we post the alert here, the details live in the app.",
            "",
            " ".join(BASE_TAGS + ["#Bursaries", "#Internships", "#Learnerships"]),
        ])
    limit = LIMITS.get(platform, 2000)
    return {"platform": platform, "kind": "roundup",
            "characters": len(text), "within_limit": len(text) <= limit,
            "text": text[:limit]}


# ------------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--feed", default=str(ROOT / "out" / "latest.json"))
    ap.add_argument("--top", type=int, default=6, help="how many get their own teaser")
    ap.add_argument("--platforms", default="instagram,x,facebook,tiktok,whatsapp")
    ap.add_argument("--out-dir", default=str(ROOT / "out"))
    args = ap.parse_args()

    feed = json.loads(Path(args.feed).read_text(encoding="utf-8"))
    items = feed["opportunities"]
    if not items:
        print("feed is empty - nothing to publish", file=sys.stderr)
        return 1
    platforms = [p.strip() for p in args.platforms.split(",") if p.strip()]
    when = feed.get("collected_for", date.today().isoformat())

    app_posts = [app_post(i) for i in items]
    featured = items[:args.top]
    payload = {
        "generated_for": when,
        "app_posts": app_posts,
        "roundup": {p: roundup(items, p, when) for p in platforms},
        "teasers": [{"opportunity": i["title"],
                     "search_term": search_term(i),
                     "closing_date": i.get("closing_date"),
                     "days_left": i.get("days_left"),
                     "captions": {p: teaser(i, p) for p in platforms}}
                    for i in featured],
    }

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"posts-{when}.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    # A readable sheet someone can copy captions straight out of.
    md = [f"# {APP_NAME} publishing pack — {when}", "",
          f"{len(app_posts)} opportunities for the app · "
          f"{len(featured)} teased on social across {', '.join(platforms)}.", "",
          "## Daily roundup posts", ""]
    for p in platforms:
        r = payload["roundup"][p]
        md += [f"### {p.title()} ({r['characters']} chars)", "", "```", r["text"], "```", ""]
    md += ["## Per-opportunity teasers", ""]
    for t in payload["teasers"]:
        md += [f"### {t['opportunity']}",
               f"Search term: **{t['search_term']}** · closes "
               f"{pretty_date(t['closing_date']) or 'not stated'}", ""]
        for p in platforms:
            c = t["captions"][p]
            flag = "" if c["within_limit"] else "  ⚠️ over limit"
            md += [f"**{p.title()}** ({c['characters']} chars){flag}", "", "```", c["text"], "```", ""]
    md += ["## App posts (full detail)", ""]
    for a in app_posts:
        md += [f"### {a['title']}", "", a["body_markdown"], "", "---", ""]
    (out_dir / f"publishing-pack-{when}.md").write_text("\n".join(md), encoding="utf-8")

    over = [f"{t['opportunity']} / {p}" for t in payload["teasers"]
            for p in platforms if not t["captions"][p]["within_limit"]]
    print(f"wrote {out_dir}/posts-{when}.json")
    print(f"wrote {out_dir}/publishing-pack-{when}.md")
    print(f"{len(app_posts)} app posts · {len(featured)} teased on {len(platforms)} platforms")
    if over:
        print("  ! over platform limit: " + ", ".join(over))
    return 0


if __name__ == "__main__":
    sys.exit(main())
