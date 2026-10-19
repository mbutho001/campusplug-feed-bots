"""Feed publishing through the token-gated post_engagement RPC (008_engagement_bots.sql).
Idempotent: the post_id is the RPC dedupe key, so a retry after a timeout never double-posts."""
import json
from datetime import datetime, timezone
from pathlib import Path

import requests

from . import db, editor

ENV_FILE = Path.home() / ".config" / "campusplug" / "app.env"


def load_env():
    env = {}
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    for k in ("CAMPUSPLUG_SUPABASE_URL", "CAMPUSPLUG_SUPABASE_PUBLISHABLE", "CAMPUSPLUG_TREND_BOT_TOKEN"):
        if not env.get(k):
            raise SystemExit(f"{k} missing from {ENV_FILE}")
    return env


def rpc(env, name, payload, timeout=30):
    key = env["CAMPUSPLUG_SUPABASE_PUBLISHABLE"]
    r = requests.post(f"{env['CAMPUSPLUG_SUPABASE_URL']}/rest/v1/rpc/{name}", json=payload, timeout=timeout,
                      headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    if r.status_code >= 400:
        try:
            msg = r.json().get("message", r.text)
        except ValueError:
            msg = r.text
        raise RuntimeError(f"{name}: HTTP {r.status_code} {msg[:200]}")
    return r.json()


PAUSE = Path(__file__).resolve().parent.parent / "state" / "PAUSE"


def publish_due(cfg, con, log, now=None, limit=6):
    if PAUSE.exists():
        log("publish: paused (state/PAUSE exists)")
        return 0
    now = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    rows = con.execute("select * from posts where status='scheduled' and scheduled_for <= ? "
                       "order by scheduled_for limit ?", (now, limit)).fetchall()
    if not rows:
        return 0
    env = load_env()
    posted = 0
    for row in rows:
        rec = db.load_record(row)
        if rec.get("expires_at") and rec["expires_at"] < now:
            rec["publication_status"] = "expired"
            db.save_post(con, rec, "expired", row["scheduled_for"])
            log(f"  expired before publishing: {rec['headline'][:60]}")
            continue
        poll = rec["poll"]["options"] if (cfg.get("native_polls") and rec["format"] == "poll" and rec.get("poll")) else None
        try:
            res = rpc(env, "post_engagement", {"p_token": env["CAMPUSPLUG_TREND_BOT_TOKEN"], "p_bot": rec["bot"],
                                               "p_content": rec.get("feed_text") or editor.feed_text(cfg, rec), "p_poll_options": poll,
                                               "p_dedupe_key": rec["post_id"]})
        except (requests.RequestException, RuntimeError) as exc:
            msg = str(exc)
            if "daily cap reached" in msg:
                log("  daily cap reached - leaving the rest scheduled")
                break
            if "unknown bot" in msg:  # database not yet updated for this bot (migration pending): keep waiting
                log(f"  waiting: {rec['bot']} not allowed by post_engagement yet (apply the pending migration)")
                continue
            attempts = rec.setdefault("attempts", 0) + 1
            rec["attempts"] = attempts
            status = "failed" if attempts >= 3 else "scheduled"
            rec["publication_status"] = status
            db.save_post(con, rec, status, row["scheduled_for"], error=msg[:300])
            log(f"  ! publish failed ({attempts}/3): {msg[:160]}")
            continue
        rec["publication_status"] = "published"
        db.save_post(con, rec, "published", row["scheduled_for"])
        con.execute("update posts set published_at=? where id=?",
                    (datetime.now(timezone.utc).isoformat(timespec="seconds"), rec["post_id"]))
        con.commit()
        posted += res == "posted"
        log(f"  {res}: [{rec['bot'].split('.')[0]}] {rec['headline'][:70]}")
    return posted
