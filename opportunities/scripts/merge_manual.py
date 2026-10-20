#!/usr/bin/env python3
"""Merge manually-fetched listings into a collected feed.

Careers Portal (and any future Cloudflare-protected source) cannot be read by
a script, so its listings arrive via WebFetch. This merges them through the
same classification, dedupe and ranking path the scripted sources use, so a
hand-fetched listing is never shaped differently from a collected one.

Input is a JSON file: either a bare list or {"source": ..., "listings": [...]}.
Each listing needs `title` and `url`; `employer`, `closing_date` (YYYY-MM-DD or
free text), `location` and `summary` are optional and are left null when absent.

    python3 merge_manual.py careersportal.json
    python3 merge_manual.py careersportal.json --feed out/latest.json
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urljoin

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import collect  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manual_file")
    ap.add_argument("--feed", default=str(ROOT / "out" / "latest.json"))
    ap.add_argument("--source", default=None, help="overrides the file's own source name")
    ap.add_argument("--base-url", default="", help="prefix for listings given as URL paths")
    args = ap.parse_args()

    data = json.loads(Path(args.manual_file).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        listings = data.get("listings", [])
        source_name = args.source or data.get("source") or "Manual"
    else:
        listings, source_name = data, (args.source or "Manual")
    base = args.base_url or (data.get("base_url", "") if isinstance(data, dict) else "")

    feed_path = Path(args.feed)
    feed = json.loads(feed_path.read_text(encoding="utf-8"))
    today = date.today()
    current = feed["opportunities"]

    added, skipped = [], {"duplicate": 0, "filtered": 0, "incomplete": 0}
    for entry in listings:
        title, url = entry.get("title"), entry.get("url") or entry.get("path")
        if not title or not url:
            skipped["incomplete"] += 1
            continue
        url = urljoin(base, url) if base else url

        closing = (entry.get("closing_date") or "").strip()
        if closing.lower() in ("not stated", "none", "unknown", "n/a", ""):
            closing = None

        item = collect.normalise({
            "source_key": source_name.lower().replace(" ", "-"),
            "source_name": source_name,
            "title": title,
            "url": url,
            "published_raw": entry.get("published", ""),
            "source_categories": entry.get("categories", []),
            "body": entry.get("summary", "") or "",
            "location_raw": entry.get("location") if
                            (entry.get("location") or "").lower() not in ("not stated", "") else None,
            "closing_raw": closing,
        }, today)

        # An employer read straight off the source page beats one guessed
        # from the title.
        if entry.get("employer") and entry["employer"].lower() != "not stated":
            item["employer"] = entry["employer"]

        ok, why = collect.is_relevant(item, today, 45)
        if not ok:
            skipped["filtered"] += 1
            print(f"  - dropped: {title[:60]} ({why})")
            continue
        item["score"] = collect.rank(item)
        # Same syndication check the scripted sources go through.
        merged_into = None
        for keeper in current + added:
            if collect.same_opportunity(keeper, item):
                merged_into = keeper
                break
        if merged_into:
            merged_into.setdefault("also_on", [])
            if source_name not in merged_into["also_on"]:
                merged_into["also_on"].append(source_name)
            for field in ("closing_date", "days_left", "province", "location", "employer"):
                if merged_into.get(field) in (None, "") and item.get(field) not in (None, ""):
                    merged_into[field] = item[field]
            skipped["duplicate"] += 1
            continue
        added.append(item)

    feed["opportunities"] = sorted(current + added,
                                   key=lambda i: -i.get("score", 0))
    feed["count"] = len(feed["opportunities"])
    feed["by_category"] = collect.tally(feed["opportunities"])
    feed["sources_needing_manual_fetch"] = [
        m for m in feed.get("sources_needing_manual_fetch", [])
        if m["name"].lower() != source_name.lower()]
    feed.setdefault("manual_merges", []).append(
        {"source": source_name, "added": len(added), "at": today.isoformat()})

    feed_path.write_text(json.dumps(feed, indent=2, ensure_ascii=False), encoding="utf-8")
    dated = feed_path.parent / f"opportunities-{feed['collected_for']}.json"
    dated.write_text(json.dumps(feed, indent=2, ensure_ascii=False), encoding="utf-8")
    collect.write_digest(feed, feed_path.parent / f"digest-{feed['collected_for']}.md")

    print(f"merged {len(added)} listings from {source_name}")
    print(f"  skipped: {skipped['duplicate']} duplicate, {skipped['filtered']} filtered, "
          f"{skipped['incomplete']} incomplete")
    print(f"feed now holds {feed['count']} opportunities")
    return 0


if __name__ == "__main__":
    sys.exit(main())
