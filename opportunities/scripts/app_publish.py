#!/usr/bin/env python3
"""Post each opportunity from posts-<date>.json to the Campus Plug app feed,
alternating between the two opportunity bots (Nomvula and Tshepo).

Always writes out/app-feed-<date>.sql (idempotent; paste into the Supabase SQL
editor as a fallback). Posts through the post_opportunity RPC from
005_opportunity_bots.sql, using ~/.config/campusplug/app.env:
CAMPUSPLUG_SUPABASE_URL, CAMPUSPLUG_SUPABASE_PUBLISHABLE (the app's public key)
and CAMPUSPLUG_OPP_BOT_TOKEN (only its SHA-256 is in the database). The RPC can
only post as the two bots - no service-role key is needed on this machine.

Re-runs never double-post: a listing whose apply URL already appears in a bot
post is skipped, both in the SQL and over the API.

    python3 scripts/app_publish.py                 # today's posts file (or latest)
    python3 scripts/app_publish.py --date 2026-10-17
    python3 scripts/app_publish.py --sql-only
    python3 scripts/app_publish.py --all           # every still-open listing from every run
    python3 scripts/app_publish.py --all --max 2   # what the hourly cron runs (spreads posts out)

--all orders by closing date (soonest first, undated last) and stops cleanly at the
RPC's 40-posts-a-day cap; whatever is left goes out on the next run.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
ENV_FILE = Path.home() / ".config" / "campusplug" / "app.env"

# Created by campusplug-app-db/005_opportunity_bots.sql.
BOT_EMAILS = ["nomvula.bot@campusplug.com", "tshepo.bot@campusplug.com"]


def load_env():
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("CAMPUSPLUG_SUPABASE_URL", "CAMPUSPLUG_SUPABASE_PUBLISHABLE", "CAMPUSPLUG_OPP_BOT_TOKEN"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def posts_file(date):
    if date:
        p = OUT / f"posts-{date}.json"
        if not p.exists():
            sys.exit(f"no {p.name}")
        return p
    today = OUT / f"posts-{dt.date.today().isoformat()}.json"
    if today.exists():
        return today
    files = sorted(OUT.glob("posts-*.json"))
    if not files:
        sys.exit("no posts-*.json in out/")
    return files[-1]


def feed_text(post):
    """Plain-text feed post. The app shows raw text, so markdown is stripped,
    and the '(N days left)' count is dropped because it goes stale once posted."""
    t = post["body_markdown"]
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)
    t = re.sub(r"(?m)^_(.+)_$", r"\1", t)
    t = re.sub(r"\s*\(\d+ days? left\)", "", t)
    # Link alone on its own line so the app can show it as a tappable link.
    t = re.sub(r"Apply / full details: (https?://\S+)", "\U0001F517 Apply / full details:\n\\1", t)
    t = re.sub(r"\s*\((?:closes )?today\)", " (closes today)", t, flags=re.I)
    emoji = {"bursary": "💰", "internship": "💼", "learnership": "🎓",
             "apprenticeship": "🔧", "graduate": "🎓", "in-service": "🛠️"}
    title = re.sub(r"\s+New$", "", post["title"])
    head = f"{emoji.get(post.get('category'), '📢')} {title}"
    return f"{head}\n\n{t.strip()}\n\n#CampusPlugOpportunities"


def sql_literal(s):
    tag = "$cp$"
    while tag in s:
        tag = tag[:-1] + "x$"
    return f"{tag}{s}{tag}"


def build_sql(posts, src_name):
    lines = [
        f"-- Campus Plug opportunity posts from {src_name}",
        f"-- Generated {dt.datetime.now():%Y-%m-%d %H:%M}. Safe to re-run: a listing whose",
        "-- apply URL is already in a bot post is skipped.",
        "-- Needs the two bots from 005_opportunity_bots.sql.",
        "begin;",
    ]
    for i, p in enumerate(posts):
        email = BOT_EMAILS[i % 2]
        url = p.get("apply_url") or ""
        lines.append(
            "insert into public.posts (user_id, content, is_approved)\n"
            f"select pr.id, {sql_literal(feed_text(p))}, true\n"
            f"from public.profiles pr where lower(pr.email) = '{email}' and pr.is_bot\n"
            "  and not exists (select 1 from public.posts x join public.profiles b on b.id = x.user_id\n"
            f"                  where b.is_bot and position({sql_literal(url)} in x.content) > 0);"
        )
    lines += ["commit;", "",
              "select pr.username, count(*) as posts from public.posts x join public.profiles pr on pr.id = x.user_id",
              f"where lower(pr.email) in ('{BOT_EMAILS[0]}', '{BOT_EMAILS[1]}') group by 1;", ""]
    return "\n".join(lines)


def api(env, method, path, body=None, prefer=None):
    base = env["CAMPUSPLUG_SUPABASE_URL"].rstrip("/")
    key = env["CAMPUSPLUG_SUPABASE_PUBLISHABLE"]
    req = urllib.request.Request(f"{base}/rest/v1/{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header("apikey", key)
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    if prefer:
        req.add_header("Prefer", prefer)
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read()
        return json.loads(data) if data else None


def norm_title(t):
    t = re.sub(r"\b(new|20\d\d)\b", "", t.lower())
    return re.sub(r"[^a-z]+", " ", t).split(":")[0].strip()


def all_open_posts(today):
    """Every still-open listing across all posts-*.json runs, one per apply URL and
    per normalised title (the same programme is often syndicated across sites).
    Undated listings only count while they are under a week old."""
    by_url = {}
    for f in sorted(OUT.glob("posts-*.json")):
        run = dt.date.fromisoformat(f.stem[len("posts-"):])
        for p in json.loads(f.read_text())["app_posts"]:
            cd = p.get("closing_date")
            if (cd and dt.date.fromisoformat(cd) >= today) or (not cd and (today - run).days <= 7):
                by_url[p.get("apply_url") or p["id"]] = p   # newest run wins
    out, titles = [], set()
    for p in sorted(by_url.values(), key=lambda p: (p.get("closing_date") or "9999", p["title"])):
        k = norm_title(p["title"])
        if k in titles:
            continue
        titles.add(k)
        out.append(p)
    return out


def publish_api(env, posts, max_new=None):
    counts = {"posted": 0, "skipped": 0}
    offset = dt.date.today().toordinal()  # alternate which bot leads each day
    for i, p in enumerate(posts):
        try:
            res = api(env, "POST", "rpc/post_opportunity", {
                "p_token": env["CAMPUSPLUG_OPP_BOT_TOKEN"],
                "p_bot": BOT_EMAILS[(i + offset) % 2],
                "p_content": feed_text(p),
                "p_url": p.get("apply_url") or "",
            })
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            if "daily cap reached" in body:
                print(f"app: daily cap reached - {len(posts) - i} left for the next run")
                break
            raise urllib.error.HTTPError(e.url, e.code, body[:300], e.headers, None)
        counts[res] = counts.get(res, 0) + 1
        if max_new and counts["posted"] >= max_new:
            break
    print(f"app: posted {counts['posted']} new, skipped {counts['skipped']} already on the feed")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--sql-only", action="store_true")
    ap.add_argument("--all", action="store_true", help="every still-open listing from every run")
    ap.add_argument("--max", type=int, help="post at most this many new listings, then stop")
    args = ap.parse_args()

    today = dt.date.today()
    if args.all:
        posts = all_open_posts(today)
        sql_path = OUT / f"app-feed-all-{today.isoformat()}.sql"
        sql_path.write_text(build_sql(posts, "all open listings"))
    else:
        src = posts_file(args.date)
        posts = json.loads(src.read_text())["app_posts"]
        # Drop anything that closed since collection.
        posts = [p for p in posts
                 if not p.get("closing_date") or dt.date.fromisoformat(p["closing_date"]) >= today]
        sql_path = OUT / src.name.replace("posts-", "app-feed-").replace(".json", ".sql")
        sql_path.write_text(build_sql(posts, src.name))
    print(f"wrote {sql_path} ({len(posts)} posts)")

    env = load_env()
    if args.sql_only:
        return 0
    if not all(env.get(k) for k in ("CAMPUSPLUG_SUPABASE_URL", "CAMPUSPLUG_SUPABASE_PUBLISHABLE",
                                     "CAMPUSPLUG_OPP_BOT_TOKEN")):
        print(f"app: settings incomplete in {ENV_FILE} - SQL file only, paste it into the SQL editor")
        return 0
    try:
        return publish_api(env, posts, args.max)
    except urllib.error.HTTPError as e:  # never break the daily mail over the feed
        print(f"app: publish failed: HTTP {e.code} {e.msg}")
        return 1
    except Exception as e:
        print(f"app: publish failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
