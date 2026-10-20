#!/usr/bin/env python3
"""Campus Plug daily user stats email: yesterday's sign-ups and real-user engagement (bots excluded).
Data comes from the token-gated RPC app_daily_stats (migration 011); sent from campusplug.qconnec@gmail.com.

    python3 stats_email.py              email yesterday's stats
    python3 stats_email.py --day 2026-10-17
    python3 stats_email.py --dry-run    write out/stats-<day>.html, send nothing
"""
import argparse
import os
import html
import json
import sys
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parent
ENV = Path.home() / ".config" / "campusplug" / "app.env"
RECIPIENTS = [a.strip() for a in os.environ.get("STATS_RECIPIENTS", "").split(",") if a.strip()]
SAST = ZoneInfo("Africa/Johannesburg")


def log(msg):
    print(f"{datetime.now(SAST):%Y-%m-%d %H:%M:%S} {msg}", flush=True)


def load_env():
    env = {}
    for line in ENV.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def fetch(day):
    env = load_env()
    key = env["CAMPUSPLUG_SUPABASE_PUBLISHABLE"]
    r = requests.post(f"{env['CAMPUSPLUG_SUPABASE_URL']}/rest/v1/rpc/app_daily_stats",
                      json={"p_token": env["CAMPUSPLUG_TREND_BOT_TOKEN"], "p_day": day.isoformat()},
                      headers={"apikey": key, "Authorization": f"Bearer {key}"}, timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"app_daily_stats: HTTP {r.status_code} {r.text[:300]}")
    return r.json()


def delta(today_v, prev_v):
    if prev_v is None:
        return ""
    d = today_v - prev_v
    return f" ({'+' if d >= 0 else ''}{d} vs day before)"


def render(s):
    e = html.escape
    days = s.get("last_7_days") or []
    prev = days[-2] if len(days) >= 2 else {}
    interactions = s["likes"] + s["comments"] + s["poll_votes"] + s["posts"] + s["replies"]
    rate = f"{100 * s['active_users'] / s['total_real_users']:.1f}%" if s["total_real_users"] else "n/a"
    rows = [
        ("New sign-ups", f"{s['signups']}{delta(s['signups'], prev.get('signups'))}"),
        ("Total real users", s["total_real_users"]),
        ("Active users (posted, commented, liked or voted)", f"{s['active_users']}{delta(s['active_users'], prev.get('active'))}"),
        ("Daily active rate", rate),
        ("Signed in", s["signed_in"]),
        ("Active last 7 days / 30 days", f"{s['active_7d']} / {s['active_30d']}"),
        ("Posts by users", s["posts"]),
        ("Replies by users", s["replies"]),
        ("Comments by users", s["comments"]),
        ("Likes by users", s["likes"]),
        ("Poll votes by users", s["poll_votes"]),
        ("Total user interactions", interactions),
        ("…of which likes / comments on bot posts", f"{s['likes_on_bot_posts']} / {s['comments_on_bot_posts']}"),
    ]
    table = "".join(f"<tr><td style='padding:4px 12px 4px 0'>{e(str(k))}</td><td><b>{e(str(v))}</b></td></tr>"
                    for k, v in rows)
    res = "".join(f"<li>{e(k)}: {v}</li>" for k, v in (s.get("signups_by_residence") or {}).items()) or "<li>none</li>"
    trend = "".join(f"<tr><td style='padding:2px 12px 2px 0'>{e(str(d['day']))}</td><td>{d['signups']}</td>"
                    f"<td>{d['active']}</td></tr>" for d in days)
    top = "".join(f"<li>{e(t['text'])}… <i>({t['author']})</i>: {t['likes']} likes, {t['comments']} comments, "
                  f"{t['votes']} votes</li>" for t in s.get("top_posts") or []) or "<li>no user interactions</li>"
    miss = s.get("timestamps_missing") or {}
    note = ""
    if any(miss.values()):
        note = (f"<p style='color:#888'>Note: {miss.get('likes', 0)} likes, {miss.get('comments', 0)} comments and "
                f"{miss.get('poll_votes', 0)} votes have no timestamp in the database and are not counted by day.</p>")
    body = (f"<h2>Campus Plug daily stats: {e(str(s['day']))}</h2>"
            f"<p>Real users only. Bot accounts and their posts, likes and comments are excluded.</p>"
            f"<table>{table}</table>"
            f"<h3>Sign-ups by residence</h3><ul>{res}</ul>"
            f"<h3>Last 7 days</h3><table><tr><th align=left>Day</th><th align=left>Sign-ups</th>"
            f"<th align=left>Active</th></tr>{trend}</table>"
            f"<h3>Most engaged posts</h3><ol>{top}</ol>"
            f"<p style='color:#888'>Bot activity for context: {s['bot_posts']} bot posts, "
            f"{s['anonymous_posts']} anonymous posts that day.</p>{note}")
    text = (f"Campus Plug {s['day']}: {s['signups']} sign-ups, {s['active_users']} active users, "
            f"{interactions} user interactions (bots excluded). See the HTML version for details.")
    subject = f"Campus Plug {s['day']}: {s['signups']} sign-ups, {s['active_users']} active users"
    return subject, body, text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", help="YYYY-MM-DD (default: yesterday, SAST)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    day = date.fromisoformat(args.day) if args.day else datetime.now(SAST).date() - timedelta(days=1)
    stats = fetch(day)
    subject, body, text = render(stats)
    (ROOT / "out").mkdir(exist_ok=True)
    (ROOT / "out" / f"stats-{day}.json").write_text(json.dumps(stats, indent=1, ensure_ascii=False))
    if args.dry_run:
        (ROOT / "out" / f"stats-{day}.html").write_text(body)
        log(f"dry run: {subject} -> out/stats-{day}.html")
        return
    sys.path.insert(0, str(Path.home() / "claude-code" / "qconnec" / "qconnec-mail"))
    from cron_sender import send
    msg = EmailMessage()
    msg["From"] = "Campus Plug Stats <campusplug.qconnec@gmail.com>"
    msg["To"] = ", ".join(RECIPIENTS)
    msg["Subject"] = subject
    msg.set_content(text)
    msg.add_alternative(body, subtype="html")
    log(f"sent '{subject}' to {', '.join(RECIPIENTS)} via {send(msg, log)}")


if __name__ == "__main__":
    main()
