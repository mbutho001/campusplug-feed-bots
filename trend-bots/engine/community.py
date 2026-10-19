"""Community desk: the five Campus Plug personas (Thandi, Sibusiso, Lerato, Kabelo, Zanele) post engagement
content all day - polls, this-or-that, hot takes, challenges, quizzes - written and independently reviewed by
free Groq/Gemini models. Replaces the old editorial-bot edge function (which needed a human review queue).
No time-sensitive facts: news belongs to the trend desk, deadlines and links to the opportunity bots."""
import hashlib
import json
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import analyst, db, llm

ROOT = Path(__file__).resolve().parent.parent
WRITE = ROOT / "prompts" / "community_write.md"
AUDIT = ROOT / "prompts" / "community_audit.md"
SAST = ZoneInfo("Africa/Johannesburg")
FORMATS = {"poll", "this_or_that", "hot_take", "fill_blank", "challenge", "quiz", "confession", "advice_swap"}
EMOJI = {"poll": "🗳️", "this_or_that": "⚖️", "hot_take": "🔥", "fill_blank": "✍️", "challenge": "💪", "quiz": "🧠",
         "confession": "🤫", "advice_swap": "💡"}


def recent_headlines(con, days=10):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    rows = con.execute("select json_extract(record, '$.headline') h from posts where slot like 'community%' "
                       "and run_date >= ? and status in ('scheduled','published','spare')", (cutoff,)).fetchall()
    return [r["h"] for r in rows if r["h"]]


def slot_times(cfg, part, n, now=None, taken=()):
    """n times (UTC ISO) inside the day-part window, never in the past, each placed in the largest free gap
    between posts already scheduled (`taken`, any bot), so top-up runs never stack posts on top of each other."""
    c = cfg["community"]
    start_h, end_h = c["windows"][part]
    today = datetime.now(SAST).date()
    day0 = datetime.combine(today, datetime.min.time(), SAST)
    start = max(day0 + timedelta(hours=start_h), (now or datetime.now(SAST)) + timedelta(minutes=5))
    end = day0 + timedelta(hours=end_h)
    if n <= 0 or start >= end:
        return []
    busy = sorted(t for t in (datetime.fromisoformat(x).astimezone(SAST) for x in taken) if start <= t <= end)
    grid = [start + timedelta(minutes=5 * i) for i in range(int((end - start).total_seconds() // 300))]
    picked = []
    for _ in range(n):
        anchors = busy + picked
        best = max(grid, key=lambda g: min([abs((g - a).total_seconds()) for a in anchors] or
                                           [(g - start).total_seconds() + (end - g).total_seconds()]))
        picked.append(best)
        grid.remove(best)
    return [t.astimezone(timezone.utc).isoformat(timespec="seconds") for t in sorted(picked)]


def feed_text(rec):
    lines = [f"{EMOJI.get(rec['format'], '💬')} {rec['headline']}", "", rec["body"]]
    poll = rec.get("poll")
    if poll and poll.get("options"):
        lines += ["", f"🗳️ {poll.get('question') or ''}".rstrip()]
        lines += [f"{'ABCD'[i]}) {o}" for i, o in enumerate(poll["options"][:4])]
        lines.append("Drop your letter in the comments 👇")
    lines += ["", f"#CampusPlug {rec['tag']}"]
    return "\n".join(lines)


def _valid(p):
    if not isinstance(p, dict) or p.get("format") not in FORMATS:
        return False
    h, b = str(p.get("headline") or ""), str(p.get("body") or "")
    if not (8 <= len(h) <= 90 and 80 <= len(b) <= 600):
        return False
    poll = p.get("poll")
    if p["format"] in ("poll", "this_or_that"):
        opts = (poll or {}).get("options") if isinstance(poll, dict) else None
        if not isinstance(opts, list) or not 2 <= len(opts) <= 4 or len({str(o).lower() for o in opts}) != len(opts):
            return False
    return True


def write(cfg, persona, part, n, avoid, log):
    """Returns (posts, writer_provider) or ([], None)."""
    today = datetime.now(SAST)
    prompt = (WRITE.read_text().replace("Write exactly N_POSTS posts.", f"Write exactly {n} posts.")
              + f"\n\nPERSONA = {persona['name']}: {persona['brief']}"
              + f"\nTODAY = {today:%A %d %B %Y} (SAST)\nDAY_PART = {part}\nN_POSTS = {n}"
              + "\nAVOID = " + json.dumps(avoid[-40:], ensure_ascii=False))
    for provider in cfg["llm"]["writer_order"]:
        if not llm.available(provider) or not llm.chain(cfg, provider, "write"):
            continue
        try:
            text, meta = llm.call_chain(cfg, provider, "write", lambda m: prompt, log, search=False)
            llm.log_usage(meta, f"community-{part}", today.date().isoformat(), "community-write")
            posts = [p for p in (llm.parse_json(text).get("posts") or []) if _valid(p)]
        except llm.LLMError as exc:
            log(f"community: {provider} failed for {persona['key']}: {str(exc)[:120]}")
            continue
        if posts:
            return posts[:n], provider
    return [], None


def audit(cfg, posts, writer, part, log):
    """Returns one (pass, reason) per post; missing verdicts fail (fail closed)."""
    provider = next((p for p in cfg["llm"]["auditor_order"]
                     if p != writer and llm.available(p) and llm.chain(cfg, p, "audit")), None)
    if provider is None:
        return [(False, "no independent free model available")] * len(posts)
    items = [{"i": i, "format": p["format"], "headline": p["headline"], "body": p["body"], "poll": p.get("poll")}
             for i, p in enumerate(posts)]
    prompt = AUDIT.read_text() + "\n\nPOSTS =\n" + json.dumps(items, ensure_ascii=False)
    for attempt in range(2):
        try:
            text, meta = llm.call_chain(cfg, provider, "audit", lambda m: prompt, log, search=False)
            llm.log_usage(meta, f"community-{part}", datetime.now(SAST).date().isoformat(), "community-audit")
            verdicts = {v.get("i"): v for v in llm.parse_json(text).get("verdicts") or [] if isinstance(v, dict)}
            break
        except llm.RateLimited as exc:
            if attempt == 0:  # every audit model hit its per-minute limit: wait for the window to reset, once
                log("community: audit models rate-limited - waiting 65 s and retrying once")
                time.sleep(65)
                llm._cooldown.clear()
                continue
            log(f"community: audit failed ({str(exc)[:120]}) - failing closed")
            return [(False, "no usable verdict")] * len(posts)
        except llm.LLMError as exc:
            log(f"community: audit failed ({str(exc)[:120]}) - failing closed")
            return [(False, "no usable verdict")] * len(posts)
    return [(verdicts.get(i, {}).get("pass") is True, f"[{meta['model']}] {str(verdicts.get(i, {}).get('reason', 'no verdict'))[:150]}")
            for i in range(len(posts))]


def run(cfg, con, part, log, dry_run=False):
    c = cfg["community"]
    avoid = recent_headlines(con)
    today = datetime.now(SAST).date()
    approved = []
    for persona in c["personas"]:
        have = con.execute("select count(*) n from posts where slot=? and run_date=? and bot=? and status in "
                           "('scheduled','published')", (f"community-{part}", today.isoformat(), persona["email"])).fetchone()["n"]
        need = c["posts_per_persona"][part] - have
        if need <= 0:
            log(f"community: {persona['key']} already has {have} {part} posts today")
            continue
        posts, writer = write(cfg, persona, part, need + 1, avoid, log)
        if not posts:
            log(f"community: nothing written for {persona['key']}")
            continue
        verdicts = audit(cfg, posts, writer, part, log)
        kept = 0
        for p, (ok, why) in zip(posts, verdicts):
            head = analyst.tokens(p["headline"])
            if ok and any(analyst.same_topic(head, analyst.tokens(a)) for a in avoid):
                ok, why = False, "too close to a recent community post"
            log(f"  {'PASS' if ok else 'FAIL'} [{persona['key']}] {p['format']}: {p['headline'][:60]} - {why[:100]}")
            if not ok or kept >= need:
                continue
            kept += 1
            avoid.append(p["headline"])
            key = hashlib.sha1(f"{persona['key']}|{p['headline']}".encode()).hexdigest()[:16]
            approved.append({
                "post_id": f"cd-{today:%Y%m%d}-{part}-{key}", "run_date": today.isoformat(), "slot": f"community-{part}",
                "cluster_key": key, "fingerprint": "", "bot": persona["email"], "persona": persona["key"],
                "format": p["format"], "topic": p.get("topic", ""), "headline": p["headline"].strip(),
                "body": p["body"].strip(), "poll": p.get("poll") if p["format"] in ("poll", "this_or_that") else None,
                "tag": persona["tag"], "audit": why, "writer": writer,
                "expires_at": datetime.combine(today, datetime.max.time(), SAST).astimezone(timezone.utc)
                .isoformat(timespec="seconds")})
    # interleave personas so the feed rotates voices
    random.shuffle(approved)
    approved.sort(key=lambda r: [p["key"] for p in c["personas"]].index(r["persona"]))
    order = []
    buckets = {}
    for r in approved:
        buckets.setdefault(r["persona"], []).append(r)
    while any(buckets.values()):
        for k in list(buckets):
            if buckets[k]:
                order.append(buckets[k].pop(0))
    taken = [r["scheduled_for"] for r in con.execute(
        "select scheduled_for from posts where status in ('scheduled','published') and scheduled_for is not null "
        "and run_date=?", (today.isoformat(),))]
    for rec, when in zip(order, slot_times(cfg, part, len(order), taken=taken)):
        rec["feed_text"] = feed_text(rec)
        rec["scheduled_for"] = when
        if not dry_run:
            db.save_post(con, rec, "scheduled", when)
        log(f"  scheduled {datetime.fromisoformat(when).astimezone(SAST):%H:%M} [{rec['persona']}] "
            f"{rec['format']}: {rec['headline'][:60]}")
    return order
