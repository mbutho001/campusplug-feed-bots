#!/usr/bin/env python3
"""Campus Plug — SA opportunities collector.

Pulls open internships, learnerships, apprenticeships, bursaries, graduate
programmes and in-service training from the SA opportunity sites, normalises
them into one shape, drops anything already published, and writes a dated
JSON feed plus a human-readable digest.

    python3 collect.py                 # collect, mark as seen, write outputs
    python3 collect.py --dry-run       # collect and write, mark nothing seen
    python3 collect.py --all           # include listings already published
    python3 collect.py --limit 40      # cap the number carried into the feed

Nothing here invents a field. A listing with no readable closing date is
emitted with closing_date: null and flagged, because Campus Plug students act
on these dates.
"""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import classify  # noqa: E402
import sources   # noqa: E402

DB_PATH = ROOT / "state" / "seen.sqlite3"
OUT_DIR = ROOT / "out"
KEEP_DAYS = 120
DEFAULT_MAX_AGE_DAYS = 45      # how far back a listing may have been published
DEFAULT_LIMIT = 60


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------------- state

def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("CREATE TABLE IF NOT EXISTS seen ("
                "id TEXT PRIMARY KEY, source TEXT, title TEXT, url TEXT, first_seen REAL)")
    con.execute("CREATE TABLE IF NOT EXISTS runs ("
                "started REAL, collected INTEGER, fresh INTEGER, note TEXT)")
    con.commit()
    return con


def item_id(url, title):
    """Identity is the URL, falling back to the title for feeds that rewrite links."""
    key = re.sub(r"[?#].*$", "", (url or "").strip().lower()) or (title or "").lower()
    return hashlib.sha1(key.encode("utf-8", "replace")).hexdigest()


def prune(con):
    con.execute("DELETE FROM seen WHERE first_seen < ?", (time.time() - KEEP_DAYS * 86400,))
    con.commit()


# ----------------------------------------------------------------- normalising

def normalise(raw, today):
    title = re.sub(r"\s+", " ", raw["title"]).strip()
    body = raw.get("body") or ""
    cats = classify.categorise(title, body, raw.get("source_categories"))

    close, snippet = (None, None)
    if raw.get("closing_raw"):
        close = classify.parse_pubdate(raw["closing_raw"])
        snippet = f"Closes: {raw['closing_raw']}"
    if not close:
        close, snippet = classify.closing_date(title, body)

    published = classify.parse_pubdate(raw.get("published_raw"))
    prov = classify.province(raw.get("location_raw") or "", title, body[:800])

    return {
        "id": item_id(raw["url"], title),
        "title": title,
        "url": raw["url"],
        "source": raw["source_name"],
        "source_key": raw["source_key"],
        "categories": cats,
        "primary_category": cats[0] if cats else None,
        "employer": classify.employer(title),
        "province": prov,
        "location": raw.get("location_raw"),
        "fields": classify.fields(title, body),
        "matric_friendly": classify.matric_only(title, body),
        "published": published.isoformat() if published else None,
        "closing_date": close.isoformat() if close else None,
        "closing_evidence": snippet,
        "days_left": classify.days_left(close, today),
        "summary": re.sub(r"\s+", " ", body)[:320].strip() or None,
        "source_categories": raw.get("source_categories", [])[:8],
    }


def is_relevant(item, today, max_age_days):
    """Keep only open, on-topic, still-live listings."""
    if not item["categories"]:
        return False, "no opportunity category matched"
    if item["days_left"] is not None and item["days_left"] < 0:
        return False, f"closed {abs(item['days_left'])} days ago"
    pub = item["published"]
    if pub:
        age = (today - date.fromisoformat(pub)).days
        if age > max_age_days:
            return False, f"published {age} days ago"
    return True, None


_TITLE_NOISE = {"the", "and", "for", "of", "a", "an", "in", "at", "to", "with",
                "south", "africa", "sa", "programme", "program", "now", "open",
                "available", "applications", "apply", "opportunity", "opportunities"}


def title_tokens(title):
    words = re.findall(r"[a-z0-9]+", (title or "").lower())
    return {w for w in words if w not in _TITLE_NOISE and not re.fullmatch(r"20\d\d", w)}


def same_opportunity(a, b):
    """True when two listings are the same programme seen on two sites.

    The same bursary is syndicated across all of these sites under different
    headlines ("Eskom Bursary Programme 2027" vs "Eskom Bursaries for
    Universities"), so exact-title matching misses most of it. Requiring the
    employer AND category to agree keeps two genuinely different programmes
    from the same employer apart unless their titles also overlap heavily.
    """
    if a["url"].rstrip("/") == b["url"].rstrip("/"):
        return True
    ta, tb = title_tokens(a["title"]), title_tokens(b["title"])
    if not ta or not tb:
        return False
    overlap = len(ta & tb) / min(len(ta), len(tb))
    ea = (a.get("employer") or "").lower().strip()
    eb = (b.get("employer") or "").lower().strip()
    employer_match = bool(ea and eb) and (ea in eb or eb in ea)
    if employer_match and a.get("primary_category") == b.get("primary_category"):
        return overlap >= 0.4
    return overlap >= 0.8


def dedupe(items):
    """Collapse syndicated repeats, keeping the highest-scoring copy.

    Pass items highest-score-first: the survivor is the copy with the best
    data (an explicit closing date outscores one without), and the sites it
    also appeared on are recorded in `also_on`.
    """
    unique = []
    for item in items:
        for keeper in unique:
            if same_opportunity(keeper, item):
                keeper.setdefault("also_on", [])
                if item["source"] not in keeper["also_on"] and item["source"] != keeper["source"]:
                    keeper["also_on"].append(item["source"])
                # Fill gaps from the duplicate rather than discarding its data.
                for field in ("closing_date", "days_left", "province", "location",
                              "employer", "closing_evidence"):
                    if keeper.get(field) in (None, "") and item.get(field) not in (None, ""):
                        keeper[field] = item[field]
                break
        else:
            unique.append(item)
    return unique


def rank(item):
    """Most urgent, most specific, most broadly useful first."""
    score = 0.0
    left = item["days_left"]
    if left is None:
        score += 4.0                       # unknown deadline - useful but unverified
    elif left <= 3:
        score += 30.0
    elif left <= 7:
        score += 24.0
    elif left <= 14:
        score += 18.0
    elif left <= 30:
        score += 12.0
    else:
        score += 6.0
    if item["closing_date"]:
        score += 6.0                       # a confirmed date is worth publishing
    if item["matric_friendly"]:
        score += 5.0                       # widest reach among Campus Plug users
    if item["employer"]:
        score += 2.0
    if item["province"]:
        score += 2.0
    score += 1.5 * len(item["fields"])
    return round(score, 2)


# -------------------------------------------------------------------- outputs

def write_json(items, failures, manual, path, today):
    payload = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "collected_for": today.isoformat(),
        "count": len(items),
        "by_category": tally(items),
        "opportunities": items,
        "sources_not_reporting": failures,
        "sources_needing_manual_fetch": manual,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def tally(items):
    counts = {}
    for it in items:
        counts[it["primary_category"] or "uncategorised"] = \
            counts.get(it["primary_category"] or "uncategorised", 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def write_digest(payload, path):
    items = payload["opportunities"]
    lines = [f"# SA opportunities — {payload['collected_for']}", "",
             f"{len(items)} open opportunities collected.", ""]
    if payload["by_category"]:
        lines.append("| Category | Count |")
        lines.append("|---|---|")
        for cat, n in payload["by_category"].items():
            lines.append(f"| {cat} | {n} |")
        lines.append("")

    by_cat = {}
    for it in items:
        by_cat.setdefault(it["primary_category"] or "uncategorised", []).append(it)

    for cat, group in by_cat.items():
        lines.append(f"## {cat.title()} ({len(group)})")
        lines.append("")
        for it in group:
            close = it["closing_date"] or "closing date not stated"
            left = f" · {it['days_left']} days left" if it["days_left"] is not None else ""
            where = f" · {it['province']}" if it["province"] else ""
            lines.append(f"- **{it['title']}**")
            lines.append(f"  {close}{left}{where} · {it['source']}")
            lines.append(f"  <{it['url']}>")
        lines.append("")

    if payload["sources_not_reporting"]:
        lines += ["## Sources not reporting this run", ""]
        lines += [f"- **{f['name']}** — {f['reason']}" for f in payload["sources_not_reporting"]]
        lines.append("")
    if payload["sources_needing_manual_fetch"]:
        lines += ["## Sources needing a manual fetch", ""]
        for m in payload["sources_needing_manual_fetch"]:
            lines.append(f"- **{m['name']}** — {m['reason']}")
            for p in m["pages"]:
                lines.append(f"  - {p}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


# ------------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="write outputs but mark nothing as seen")
    ap.add_argument("--all", action="store_true", help="include already-published listings")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    ap.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS)
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    args = ap.parse_args()

    today = date.today()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    log(f"Campus Plug opportunity collector — {today.isoformat()}")
    raw_items, failures = sources.collect_all(log)
    log(f"\n{len(raw_items)} raw listings from {len(sources.FEED_SOURCES) + 1} sources")

    con = connect()
    seen_ids = {r[0] for r in con.execute("SELECT id FROM seen")}

    kept, dropped, repeats = [], {}, 0
    for raw in raw_items:
        item = normalise(raw, today)
        ok, why = is_relevant(item, today, args.max_age_days)
        if not ok:
            dropped[why] = dropped.get(why, 0) + 1
            continue
        if item["id"] in seen_ids and not args.all:
            repeats += 1
            continue
        item["score"] = rank(item)
        kept.append(item)

    unique = dedupe(sorted(kept, key=lambda i: -i["score"]))
    log(f"{len(kept) - len(unique)} cross-source duplicates merged")

    final = unique[:args.limit]
    log(f"{len(final)} fresh opportunities kept "
        f"({repeats} already published, {sum(dropped.values())} filtered out)")
    for why, n in sorted(dropped.items(), key=lambda kv: -kv[1]):
        log(f"    filtered: {n} × {why}")

    stamp = today.isoformat()
    payload = write_json(final, failures, sources.BLOCKED_SOURCES,
                         out_dir / f"opportunities-{stamp}.json", today)
    write_digest(payload, out_dir / f"digest-{stamp}.md")
    (out_dir / "latest.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    if not args.dry_run:
        for item in final:
            con.execute("INSERT OR IGNORE INTO seen VALUES (?,?,?,?,?)",
                        (item["id"], item["source_key"], item["title"][:250],
                         item["url"], time.time()))
        con.execute("INSERT INTO runs VALUES (?,?,?,?)",
                    (time.time(), len(raw_items), len(final), "ok"))
        con.commit()
        prune(con)
    else:
        log("dry run: nothing marked as seen")

    log(f"\nwrote {out_dir}/opportunities-{stamp}.json")
    log(f"wrote {out_dir}/digest-{stamp}.md")
    for f in failures:
        log(f"  ! not reporting: {f['name']} — {f['reason']}")
    for m in sources.BLOCKED_SOURCES:
        log(f"  → manual fetch needed: {m['name']} ({m['reason']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
