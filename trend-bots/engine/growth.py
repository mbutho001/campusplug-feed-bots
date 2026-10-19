"""Agent F - Growth analyst: pull real engagement (non-bot users only), compare categories / formats /
slots, and nudge category multipliers only when there is enough real data to justify it.

Safety rules and auto-publish permissions are never changed here."""
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from . import db, publisher

ROOT = Path(__file__).resolve().parent.parent
LEARNED = ROOT / "state" / "learned.json"
MIN_POSTS_PER_GROUP = 8
MIN_TOTAL_INTERACTIONS = 30
STEP, LOW, HIGH = 0.05, 0.8, 1.2


def collect(con, log, days=14):
    env = publisher.load_env()
    stats = publisher.rpc(env, "engagement_bot_stats", {"p_token": env["CAMPUSPLUG_TREND_BOT_TOKEN"], "p_days": days})
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for s in stats:
        m = {"likes": s["likes"], "comments": s["comments"], "poll_votes": s["poll_votes"], "measured_at": stamp,
             "note": "non-bot users only; impressions/reach/shares/saves are not tracked by the app"}
        con.execute("update posts set metrics=? where id=?", (json.dumps(m), s["dedupe_key"]))
    con.commit()
    log(f"growth: metrics for {len(stats)} published posts")
    return len(stats)


def summarise(con, days=7):
    rows = con.execute("select * from posts where status='published' and run_date >= date('now', ?)",
                       (f"-{days} days",)).fetchall()
    groups = {"category": defaultdict(list), "format": defaultdict(list), "slot": defaultdict(list),
              "bot": defaultdict(list)}
    total = 0
    for r in rows:
        rec = db.load_record(r)
        m = json.loads(r["metrics"]) if r["metrics"] else {}
        inter = m.get("likes", 0) + 2 * m.get("comments", 0) + m.get("poll_votes", 0)
        total += inter
        for g, key in (("category", rec.get("category") or rec.get("persona", "community")), ("format", rec["format"]), ("slot", rec["slot"]),
                       ("bot", (rec.get("bot") or "?").split(".")[0])):
            groups[g][key].append(inter)
    table = {g: {k: {"posts": len(v), "avg": round(sum(v) / len(v), 2)} for k, v in d.items()}
             for g, d in groups.items()}
    return {"posts": len(rows), "interactions": total, "groups": table}


def learn(con, cfg, summary, log):
    """Bounded multiplier nudges; skipped (and said so) without enough real engagement."""
    if summary["interactions"] < MIN_TOTAL_INTERACTIONS:
        msg = (f"insufficient real engagement to learn from ({summary['interactions']} interactions from "
               f"non-bot users in {summary['posts']} posts; need {MIN_TOTAL_INTERACTIONS})")
        log("growth: " + msg)
        return msg
    cats = summary["groups"]["category"]
    eligible = {k: v for k, v in cats.items() if v["posts"] >= MIN_POSTS_PER_GROUP}
    if len(eligible) < 2:
        return "not enough posts per category to compare yet"
    mean = sum(v["avg"] for v in eligible.values()) / len(eligible)
    learned = json.loads(LEARNED.read_text()) if LEARNED.exists() else {}
    changes = []
    for k, v in eligible.items():
        cur = learned.get(k, 1.0)
        new = cur + STEP if v["avg"] > mean * 1.25 else cur - STEP if v["avg"] < mean * 0.75 else cur
        new = round(min(HIGH, max(LOW, new)), 2)
        if new != cur:
            changes.append(f"{k} {cur}->{new} (avg {v['avg']} vs mean {mean:.2f}, n={v['posts']})")
            learned[k] = new
    if changes:
        LEARNED.write_text(json.dumps(learned, indent=1))
        con.execute("insert into experiments (started, hypothesis, change) values (?,?,?)",
                    (datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     "categories with above-average real engagement deserve more feed slots",
                     "; ".join(changes)))
        con.commit()
    return "; ".join(changes) or "no change: categories within ±25% of the mean"


def multipliers():
    return json.loads(LEARNED.read_text()) if LEARNED.exists() else {}
