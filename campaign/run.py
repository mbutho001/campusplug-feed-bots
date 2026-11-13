#!/usr/bin/env python3
"""Campus Plug traction campaign: prizes + registration + follows, daily polls and brand battles,
Mzansi pulse reposts, and the weekly Plug Pulse report. Runs on the feed-bots tick (tick.py).

    python3 run.py make  [--day D]   06:45  today's plan + posters + UGC-style Short (out/<day>/)
    python3 run.py pulse [--day D]   14:15  add the best audited trend-desk post of the day to the plan
    python3 run.py post  [--dry-run] ticks  post every plan item whose time has come (once each)
    python3 run.py collect           21:45  poll results from the app, Threads and X -> state/poll-results.json
    python3 run.py report [--dry-run] Mon 08:30  Plug Pulse email + Monday results post

Intermittent by design: promo on config.promo.days, videos on video_days, a poll every day, a pulse
post every day. Kill switch: create campaign/state/PAUSE in the state repo (nothing posts).
Prize claims: see prizes.json and captions.guard() - teaser mode until a prize is funded + has rules.
"""
import argparse
import json
import os
import sqlite3
import sys
import tempfile
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(REPO / "opportunities" / "scripts"))
sys.path.insert(0, str(REPO / "trend-bots"))

import captions  # noqa: E402

SAST = ZoneInfo("Africa/Johannesburg")
STATE = ROOT / "state"
OUT = ROOT / "out"
GRACE_HOURS = 3
LETTERS = "ABCD"
APP_BOTS = ["zanele.bot@campusplug.com", "lerato.bot@campusplug.com", "sibusiso.bot@campusplug.com",
            "kabelo.bot@campusplug.com", "thandi.bot@campusplug.com"]


def log(msg):
    print(f"[{datetime.now(SAST):%H:%M}] {msg}", flush=True)


def jload(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def jsave(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")


def cfg():
    return captions.load_json("config.json")


def prizes():
    return captions.load_json("prizes.json")["prizes"]


def in_window(c, day):
    return c["start"] <= day.isoformat() <= c["end"]


def plan_path(day):
    return OUT / day.isoformat() / "plan.json"


def days_since(c, day, weekdays):
    """How many of `weekdays` fell between the start and `day` (exclusive): the rotation index."""
    d, n = date.fromisoformat(c["start"]), 0
    while d < day:
        n += d.weekday() in weekdays
        d += timedelta(days=1)
    return n


def tracked(platform):
    from fliers import PLAY_URL
    return (f"{PLAY_URL}&referrer=utm_source%3D{platform}%26utm_medium%3Dsocial%26utm_campaign%3Dprize_drops")


# ---------------------------------------------------------------- make

def make(day, voice=True):
    import render
    c, pz = cfg(), prizes()
    if not in_window(c, day):
        log(f"make: {day} outside the campaign window {c['start']}..{c['end']}")
        return 0
    folder = OUT / day.isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    wd, pr, po = day.weekday(), c["promo"], c["polls"]
    ph = captions.phase(day)
    items = []
    with tempfile.TemporaryDirectory() as tmp:
        if wd in pr["days"] or wd in pr["story_days"] or wd in pr["video_days"]:
            order = captions.ANGLES_BY_PHASE[ph["key"]]
            k = order[days_since(c, day, set(pr["days"]) | set(pr["story_days"])) % len(order)]
            a = captions.angle(day, k, pz, c)
            caption, writer = captions.freshen(a, log)
            files = {}
            for fmt in ("portrait", "square", "story"):
                files[fmt] = str(render.promo_poster(fmt, a, pz, folder / f"promo-{k}-{fmt}.png", tmp).relative_to(ROOT))
            meta = {"angle": k, "phase": ph["key"], "live": a["live"], "writer": writer}
            if wd in pr["days"]:
                items.append(dict(id="promo-feed", time=pr["time"], platforms=["facebook", "instagram", "threads"],
                                  files=[files["portrait"]], caption=caption, **meta))
            if wd in pr["x_days"]:
                items.append(dict(id="promo-x", time=pr["x_time"], platforms=["x"], files=[files["square"]],
                                  caption=(a["short"] + "\nFree app, link in bio")[:280], **meta))
            if wd in pr["whatsapp_days"]:
                items.append(dict(id="promo-wa", time=pr["whatsapp_time"], platforms=["whatsapp"],
                                  files=[files["square"]], caption=f"{a['short']}\n\nGet Campus Plug: {tracked('whatsapp')}",
                                  **meta))
            if wd in pr["linkedin_days"]:
                items.append(dict(id="promo-li", time=pr["time"], platforms=["linkedin"], files=[files["portrait"]],
                                  caption=f"{caption}\n\nGet Campus Plug: {tracked('linkedin')}", **meta))
            if wd in pr["story_days"]:
                items.append(dict(id="promo-story", time=pr["story_time"], platforms=["facebook_story", "instagram_story"],
                                  files=[files["story"]], caption=a["short"], **meta))
        if wd in pr["video_days"]:
            order = captions.ANGLES_BY_PHASE[ph["key"]]
            k = order[(days_since(c, day, set(pr["video_days"])) + 2) % len(order)]
            a = captions.angle(day, k, pz, c)
            beats, vo = captions.SHORTS[a["video"]]
            video = folder / f"short-{k}.mp4"
            try:
                secs = render.ugc_short(beats, vo, video, voice=voice)
                log(f"make: Short {video.name} {secs:.1f}s")
                text = f"{a['short']} {captions.TAGS} #PlugSeasons"
                items.append(dict(id="promo-video", time=pr["video_time"],
                                  platforms=["tiktok", "youtube", "facebook_video"], files=[str(video.relative_to(ROOT))],
                                  caption=text, title=(a["headline"].replace("*", "") + " | Campus Plug")[:95],
                                  tags=["Campus Plug", "South Africa", "students", "bursaries", "Plug Seasons"],
                                  angle=k, phase=ph["key"], live=a["live"]))
            except Exception as exc:  # noqa: BLE001 - a video failure never stops the posters
                log(f"make: Short FAILED {exc!r}")
        # Daily poll: a brand battle on battle_days, a topic poll otherwise.
        bank = captions.load_json("polls.json")
        used = jload(STATE / "polls-used.json", {"battle": 0, "topic": 0, "days": {}})
        if day.isoformat() in used["days"]:
            poll = used["days"][day.isoformat()]
        else:
            kind = "battle" if wd in po["battle_days"] else "topic"
            pool = bank["battles" if kind == "battle" else "topics"]
            p = pool[used[kind] % len(pool)]
            used[kind] += 1
            poll = {"kind": kind, **p}
            used["days"][day.isoformat()] = poll
            jsave(STATE / "polls-used.json", used)
        key = f"cp-poll-{day.isoformat()}-{poll['id']}"
        bot = APP_BOTS[0] if poll["kind"] == "battle" else APP_BOTS[1 + date.fromisoformat(day.isoformat()).toordinal() % 4]
        label = "Brand battle" if poll["kind"] == "battle" else "Daily poll"
        app_text = (f"🗳️ {label}: {poll['question']}\n\nTap your answer above. One vote each, results every Monday "
                    f"on our socials (#PlugPulse).\n\n#CampusPlug #PlugPulse")
        items.append(dict(id="poll-app", time=po["app_time"], platforms=["app_poll"], poll=poll, dedupe=key,
                          bot=bot, caption=app_text))
        items.append(dict(id="poll-threads", time=po["threads_time"], platforms=["threads_poll"], poll=poll,
                          caption=f"{label}: {poll['question']}\n\nVote here or in the Campus Plug app. Results Monday. #PlugPulse"))
        if wd in po["x_days"]:
            items.append(dict(id="poll-x", time=po["threads_time"], platforms=["x_poll"], poll=poll,
                              caption=f"{label}: {poll['question']} #PlugPulse"[:280]))
        if wd in po["poster_days"]:
            f = render.poll_poster("portrait", poll["question"], poll["options"], label,
                                   folder / f"poll-{poll['id']}-portrait.png", tmp)
            items.append(dict(id="poll-poster", time=po["poster_time"], platforms=["facebook", "instagram"],
                              files=[str(f.relative_to(ROOT))],
                              caption=(f"{label}: {poll['question']}\n\n" + "\n".join(
                                  f"{LETTERS[i]}) {o}" for i, o in enumerate(poll["options"]))
                                  + "\n\nComment your letter or vote in the Campus Plug app. Results every Monday.\n\n"
                                  + captions.TAGS + " #PlugPulse")))
        # Monday: last week's results, if enough people voted.
        if wd == c["report"]["weekday"]:
            res = results_item(day, folder, tmp)
            if res:
                items.append(res)
    plan = jload(plan_path(day), {"date": day.isoformat(), "items": []})
    keep = [i for i in plan["items"] if i["id"].startswith("pulse")]
    plan["items"] = items + keep
    jsave(plan_path(day), plan)
    log(f"make: {len(items)} items for {day} ({ph['name']}): " + ", ".join(i["id"] for i in items))
    return 0


def results_item(day, folder, tmp):
    import render
    results = jload(STATE / "poll-results.json", {})
    week = [(k, r) for k, r in results.items() if (day - date.fromisoformat(r["date"])).days in range(1, 8)]
    week = [(k, r) for k, r in week if r.get("total", 0) >= 5]
    if not week:
        log("make: no poll last week reached 5 votes - no results post")
        return None
    week.sort(key=lambda kr: (kr[1]["kind"] != "battle", -kr[1]["total"]))
    k, r = week[0]
    lines = [f"{o}: {pct}%" for o, pct in zip(r["options"], r["pct"])]
    f = render.results_poster("portrait", r["question"], r["options"], r["pct"], r["total"],
                              folder / f"results-{r['id']}-portrait.png", tmp)
    return dict(id="results", time="12:00", platforms=["facebook", "instagram", "threads"], files=[str(f.relative_to(ROOT))],
                caption=(f"Plug Pulse results: {r['question']}\n\n" + "\n".join(lines)
                         + f"\n\nBased on {r['total']} votes from Campus Plug students ({', '.join(r['sources'])}), "
                         "not a scientific sample. New poll every morning in the app.\n\n" + captions.TAGS + " #PlugPulse"))


# ---------------------------------------------------------------- pulse (trend-desk repost)

def pulse(day):
    import render
    c = cfg()
    if not in_window(c, day):
        return 0
    pc = c["pulse"]
    cats = set(pc["categories_by_weekday"][str(day.weekday())])
    db = REPO / "trend-bots" / "state" / "engine.db"
    if not db.exists():
        log("pulse: no trend-desk engine.db")
        return 0
    con = sqlite3.connect(db)
    best, fallback = None, None
    for (rec,) in con.execute("select record from posts where status='published' and run_date=?", (day.isoformat(),)):
        r = json.loads(rec)
        if r.get("risk") != "low" or r.get("verification_status") not in ("confirmed", "corroborated"):
            continue
        if (r.get("confidence") or 0) < pc["min_confidence"] or r.get("approval_status") != "auto_approved":
            continue
        score = r.get("trend_score") or 0
        if r.get("category") in cats:
            if best is None or score > (best.get("trend_score") or 0):
                best = r
        elif r.get("category") not in ("politics", "public_safety") and \
                (fallback is None or score > (fallback.get("trend_score") or 0)):
            fallback = r
    best = best or fallback
    if not best:
        log(f"pulse: nothing published today in {sorted(cats)} that is low-risk + confirmed")
        return 0
    label = pc["label_by_weekday"][str(day.weekday())]
    folder = OUT / day.isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    summary = (best.get("formats") or {}).get("summary") or best.get("body", "")
    summary = summary if len(summary) <= 260 else summary[:257].rsplit(" ", 1)[0] + "..."
    outlet = best.get("attribution", "").replace("Reporting: ", "") or best.get("source_outlet", "")
    with tempfile.TemporaryDirectory() as tmp:
        f = render.pulse_card("portrait", label, best["headline"], summary, outlet,
                              folder / f"pulse-{best['post_id'][-8:]}.png", tmp)
    question = (best.get("poll") or {}).get("question") or best.get("caption") or "What do you think?"
    long = (f"{best['headline']}\n\n{summary}\n\n{question} Tell us below, or join the conversation in the "
            f"Campus Plug app.\n\nSource: {outlet} {best.get('source_url', '')}\n\n#CampusPlug #Mzansi "
            + " ".join("#" + t.replace(" ", "") for t in (best.get("tags") or [])[:3]))
    items = [dict(id="pulse", time=pc["time"], platforms=pc["platforms"], files=[str(f.relative_to(ROOT))],
                  caption=long, source=best["post_id"], label=label)]
    if pc.get("x"):
        x = f"{label}: {best['headline']}. {question} (via {outlet})"
        items.append(dict(id="pulse-x", time=pc["time"], platforms=["x"], files=[str(f.relative_to(ROOT))],
                          caption=x[:280], source=best["post_id"], label=label))
    plan = jload(plan_path(day), {"date": day.isoformat(), "items": []})
    plan["items"] = [i for i in plan["items"] if not i["id"].startswith("pulse")] + items
    jsave(plan_path(day), plan)
    log(f"pulse: {label} '{best['headline'][:70]}' ({best.get('category')}, {outlet})")
    return 0


# ---------------------------------------------------------------- posting

def app_env():
    from engine import publisher
    return publisher


def post_item(item, platform, day, done):
    """Posts one item to one platform. Returns (id, note)."""
    import channel_post
    import meta_post
    import pro_post
    files = [ROOT / f for f in item.get("files", [])]
    files = [f for f in files if f.exists()]
    cap = item.get("caption", "")
    if platform in ("facebook", "instagram", "threads", "facebook_story", "instagram_story", "facebook_video"):
        tok = meta_post.page_token()
        if platform != "threads" and not tok:
            return None, "META_PAGE_TOKEN not set"
        if platform in ("facebook", "instagram", "facebook_story", "instagram_story", "facebook_video"):
            if "page" not in done:
                pid, _, ig, _ = meta_post.page_info(tok)
                done["page"], done["ig"] = pid, ig
        if platform == "facebook":
            fb_cap = cap.replace("link in bio.", "→ " + tracked("facebook")).replace("link in bio", "→ " + tracked("facebook"))
            pid, urls = meta_post.fb_post(done["page"], tok, files, fb_cap)
            done["urls"] = urls
            return pid, "facebook"
        if platform == "instagram":
            urls = done.get("urls")
            if not urls:
                _, url = meta_post.fb_upload(done["page"], tok, files[0])
                urls = done["urls"] = [url]
            return meta_post.ig_post(done["ig"], tok, urls, cap), "instagram"
        if platform == "threads":
            ttok = meta_post.threads_token()
            if not ttok:
                return None, "THREADS_TOKEN not set"
            urls = done.get("urls")
            if not urls and tok:
                if "page" not in done:
                    done["page"], _, done["ig"], _ = meta_post.page_info(tok)
                _, url = meta_post.fb_upload(done["page"], tok, files[0])
                urls = done["urls"] = [url]
            return meta_post.threads_post(ttok, urls, cap), "threads"
        if platform in ("facebook_story", "instagram_story"):
            if "story_upload" not in done:
                done["story_upload"] = list(meta_post.fb_upload(done["page"], tok, files[0]))
            photo, url = done["story_upload"]
            if platform == "facebook_story":
                return meta_post.fb_story(done["page"], tok, photo), "facebook story"
            return meta_post.ig_story(done["ig"], tok, url), "instagram story"
        if platform == "facebook_video":
            r = meta_post.call("POST", f"https://graph-video.facebook.com/v23.0/{done['page']}/videos",
                               {"description": cap + "\n\nGet Campus Plug: " + tracked("facebook"),
                                "title": item.get("title", ""), "access_token": tok},
                               files={"source": files[0]}, timeout=600)
            return r.get("id"), "facebook video"
    if platform == "x":
        return pro_post.x_post(cap, files[:1])
    if platform == "linkedin":
        return pro_post.linkedin_post(cap, files)
    if platform == "whatsapp":
        return channel_post.whatsapp_post({"caption": cap}, files)
    if platform == "tiktok":
        return channel_post.tiktok_post({"caption": cap}, files[0])
    if platform == "youtube":
        return channel_post.youtube_post({"title": item["title"], "caption": cap, "tags": item.get("tags", [])}, files[0])
    if platform == "app_poll":
        pub = app_env()
        env = pub.load_env()
        res = pub.rpc(env, "post_engagement", {"p_token": env["CAMPUSPLUG_TREND_BOT_TOKEN"], "p_bot": item["bot"],
                                               "p_content": cap, "p_poll_options": item["poll"]["options"],
                                               "p_dedupe_key": item["dedupe"]})
        register_poll(day, item, "app", item["dedupe"])
        return res, "app feed"
    if platform == "threads_poll":
        ttok = meta_post.threads_token()
        if not ttok:
            return None, "THREADS_TOKEN not set"
        opts = {f"option_{'abcd'[i]}": o[:25] for i, o in enumerate(item["poll"]["options"][:4])}
        cid = meta_post.call("POST", f"{meta_post.THREADS}/me/threads",
                             {"media_type": "TEXT", "text": meta_post.threads_caption(cap),
                              "poll_attachment": json.dumps(opts), "access_token": ttok})["id"]
        meta_post.wait_ready(meta_post.THREADS, cid, ttok, field="status")
        tid = meta_post.call("POST", f"{meta_post.THREADS}/me/threads_publish",
                             {"creation_id": cid, "access_token": ttok}).get("id")
        register_poll(day, item, "threads", tid)
        return tid, "threads poll"
    if platform == "x_poll":
        keys = pro_post.x_keys()
        if not keys:
            return None, "X_* secrets not set"
        body = {"text": cap, "poll": {"options": [o[:25] for o in item["poll"]["options"][:4]], "duration_minutes": 1440}}
        r = pro_post._x_request("POST", f"{pro_post.X_API}/tweets", keys, json.dumps(body).encode(), "application/json")
        tid = (r.get("data") or {}).get("id")
        register_poll(day, item, "x", tid)
        return tid, "x poll"
    return None, f"unknown platform {platform}"


def register_poll(day, item, source, ref):
    reg = jload(STATE / "polls.json", {})
    p = item["poll"]
    e = reg.setdefault(f"{day.isoformat()}#{p['id']}", {"date": day.isoformat(), "id": p["id"], "kind": p["kind"],
                                                        "question": p["question"], "options": p["options"],
                                                        "for": p.get("for", []), "category": p.get("category", ""),
                                                        "refs": {}})
    e["refs"][source] = ref if source != "app" else item["dedupe"]
    jsave(STATE / "polls.json", reg)


def post(dry=False, force_ids=()):
    if (STATE / "PAUSE").exists():
        log("post: paused (campaign/state/PAUSE)")
        return 0
    now = datetime.now(SAST)
    day = now.date()
    plan = jload(plan_path(day), None)
    if not plan:
        return 0
    posted = jload(STATE / "posted.json", {})
    failures = 0
    for item in plan["items"]:
        h, m = map(int, item["time"].split(":"))
        at = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if item["id"] not in force_ids and not (at <= now < at + timedelta(hours=GRACE_HOURS)):
            continue
        key = f"{day.isoformat()}#{item['id']}"
        done = posted.setdefault(key, {})
        for platform in item["platforms"]:
            if platform in done or f"{platform}!" in done:
                continue
            if dry:
                log(f"[dry-run] {item['time']} {item['id']} -> {platform}: {item.get('caption', '')[:120]!r}")
                continue
            try:
                pid, note = post_item(item, platform, day, done)
                if pid is None:
                    log(f"{key} {platform}: skipped ({note})")
                    continue
                done[platform] = pid
                log(f"{key} {platform}: posted {pid} ({note})")
            except Exception as exc:  # noqa: BLE001 - one platform failing never stops the others
                failures += 1
                done[f"{platform}!"] = str(exc)[:300]   # attempted: never retried every 10 minutes
                log(f"{key} {platform}: FAILED {exc}")
            jsave(STATE / "posted.json", posted)
    cutoff = (day - timedelta(days=60)).isoformat()
    posted = {k: v for k, v in posted.items() if k.split("#")[0] >= cutoff}
    if not dry:
        jsave(STATE / "posted.json", posted)
    return 1 if failures else 0


# ---------------------------------------------------------------- results

def collect():
    import meta_post
    import pro_post
    reg = jload(STATE / "polls.json", {})
    results = jload(STATE / "poll-results.json", {})
    today = datetime.now(SAST).date()
    recent = {k: e for k, e in reg.items() if (today - date.fromisoformat(e["date"])).days <= 14}
    app_split, app_total = {}, {}
    if any("app" in e["refs"] for e in recent.values()):
        pub = app_env()
        env = pub.load_env()
        try:
            rows = pub.rpc(env, "engagement_poll_results", {"p_token": env["CAMPUSPLUG_TREND_BOT_TOKEN"], "p_days": 15})
            app_split = {r["dedupe_key"]: r["counts"] for r in rows}
        except Exception as exc:  # noqa: BLE001 - migration 027 not applied yet: totals only
            log(f"collect: per-option app results unavailable ({exc}); using totals")
            rows = pub.rpc(env, "engagement_bot_stats", {"p_token": env["CAMPUSPLUG_TREND_BOT_TOKEN"], "p_days": 15})
            app_total = {r["dedupe_key"]: r["poll_votes"] for r in rows}
    ttok = meta_post.threads_token()
    xkeys = pro_post.x_keys()
    for k, e in recent.items():
        n = len(e["options"])
        counts, sources, total_only = [0] * n, [], 0
        ref = e["refs"].get("app")
        if ref in app_split:
            for i, v in enumerate(app_split[ref][:n]):
                counts[i] += int(v or 0)
            sources.append("app")
        elif ref in app_total:
            total_only += int(app_total[ref] or 0)
            sources.append("app (total only)")
        if ttok and e["refs"].get("threads"):
            try:
                f = ",".join(f"option_{c}_votes_percentage" for c in "abcd"[:n]) + ",total_votes"
                r = meta_post.call("GET", f"{meta_post.THREADS}/{e['refs']['threads']}",
                                   {"fields": f"poll_attachment{{{f}}}", "access_token": ttok})
                pa = r.get("poll_attachment") or {}
                tv = int(pa.get("total_votes") or 0)
                share = [float(pa.get(f"option_{c}_votes_percentage") or 0) for c in "abcd"[:n]]
                scale = 100 if sum(share) > 1.5 else 1      # percent or fraction, whichever the API sends
                for i, v in enumerate(share):
                    counts[i] += round(tv * v / scale)
                if tv:
                    sources.append("Threads")
            except Exception as exc:  # noqa: BLE001
                log(f"collect {k} threads: {exc}")
        if xkeys and e["refs"].get("x"):
            try:
                r = pro_post._x_request("GET", f"{pro_post.X_API}/tweets/{e['refs']['x']}"
                                        "?expansions=attachments.poll_ids&poll.fields=options,voting_status", xkeys)
                for poll in (r.get("includes") or {}).get("polls", []):
                    for o in poll.get("options", []):
                        if 1 <= o.get("position", 0) <= n:
                            counts[o["position"] - 1] += int(o.get("votes") or 0)
                    sources.append("X")
            except Exception as exc:  # noqa: BLE001
                log(f"collect {k} x: {exc}")
        split_total = sum(counts)
        results[k] = {**{x: e[x] for x in ("date", "id", "kind", "question", "options", "for", "category")},
                      "counts": counts, "split_total": split_total, "total": split_total + total_only,
                      "pct": [round(100 * v / split_total) if split_total else 0 for v in counts],
                      "sources": sources or ["none yet"], "collected": today.isoformat()}
    jsave(STATE / "poll-results.json", results)
    log(f"collect: {len(recent)} polls updated")
    return 0


def report(dry=False):
    c = cfg()
    today = datetime.now(SAST).date()
    results = jload(STATE / "poll-results.json", {})
    week = sorted((r for r in results.values() if (today - date.fromisoformat(r["date"])).days in range(1, 8)),
                  key=lambda r: (r["kind"] != "battle", r["date"]))
    posted = jload(STATE / "posted.json", {})
    activity = {}
    for k, v in posted.items():
        if (today - date.fromisoformat(k.split("#")[0])).days in range(1, 8):
            for p in v:
                if p in ("page", "ig", "urls", "story_upload"):
                    continue
                ok = not p.endswith("!")
                activity.setdefault(p.rstrip("!"), [0, 0])[0 if ok else 1] += 1
    small = c["report"]["small_sample"]
    h = ["<h2>Plug Pulse: week to " + today.isoformat() + "</h2>",
         "<p>Poll results from Campus Plug students (app feed, plus Threads and X where posted). Counts exclude "
         "bot accounts. " + (f"Samples under {small} votes are indicative only, not representative." if small else "")
         + "</p>"]
    for r in week:
        brands = f" <i>(for: {', '.join(r['for'])})</i>" if r.get("for") else ""
        h.append(f"<h3>{'Brand battle' if r['kind'] == 'battle' else 'Topic'}: {r['question']}{brands}</h3>"
                 f"<p>{r['date']} · {r['total']} votes · sources: {', '.join(r['sources'])}"
                 + (" · <b>small sample</b>" if r["total"] < small else "") + "</p><table border=1 cellpadding=4>"
                 + "".join(f"<tr><td>{o}</td><td>{n}</td><td>{p}%</td></tr>"
                           for o, n, p in zip(r["options"], r["counts"], r["pct"])) + "</table>")
    if not week:
        h.append("<p>No polls were collected last week.</p>")
    h.append("<h3>Campaign posts last 7 days</h3><table border=1 cellpadding=4><tr><th>Platform</th><th>Posted</th>"
             "<th>Failed</th></tr>" + "".join(f"<tr><td>{p}</td><td>{a}</td><td>{b}</td></tr>"
                                             for p, (a, b) in sorted(activity.items())) + "</table>")
    live = [p["name"] for p in prizes() if p["status"] == "live"]
    h.append(f"<p>Prize mode: <b>{'LIVE: ' + ', '.join(live) if live else 'TEASER (no prize funded yet)'}</b>. "
             "Edit campaign/prizes.json to go live.</p>")
    body = "\n".join(h)
    out = STATE / "reports"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"pulse-{today}.html").write_text(body, encoding="utf-8")
    with open(out / f"pulse-{today}.csv", "w", encoding="utf-8") as fh:
        fh.write("date,kind,category,question,option,votes,pct,total,sources\n")
        for r in week:
            for o, n, p in zip(r["options"], r["counts"], r["pct"]):
                fh.write(",".join('"' + str(x).replace('"', "'") + '"' for x in
                                  (r["date"], r["kind"], r.get("category", ""), r["question"], o, n, p, r["total"],
                                   "+".join(r["sources"]))) + "\n")
    if dry:
        log(f"report (dry): {len(week)} polls -> {out}/pulse-{today}.html")
        return 0
    rcpt = [a.strip() for a in (os.environ.get("STATS_RECIPIENTS") or os.environ.get("DIGEST_RECIPIENTS") or "").split(",")
            if a.strip()]
    if not rcpt:
        log("report: no STATS_RECIPIENTS - written to state only")
        return 0
    from email.message import EmailMessage
    sys.path.insert(0, str(Path.home() / "claude-code" / "qconnec" / "qconnec-mail"))
    from cron_sender import send
    msg = EmailMessage()
    msg["From"] = "Campus Plug Pulse <campusplug.qconnec@gmail.com>"
    msg["To"] = ", ".join(rcpt)
    msg["Subject"] = f"Plug Pulse {today}: {len(week)} polls, {sum(r['total'] for r in week)} votes"
    msg.set_content("See the HTML version.")
    msg.add_alternative(body, subtype="html")
    msg.add_attachment((out / f"pulse-{today}.csv").read_bytes(), maintype="text", subtype="csv",
                       filename=f"plug-pulse-{today}.csv")
    log(f"report: sent via {send(msg, log)}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["make", "pulse", "post", "collect", "report"])
    ap.add_argument("--day")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-voice", action="store_true")
    ap.add_argument("--force", nargs="*", default=(), help="post: these item ids whatever the time")
    a = ap.parse_args()
    day = date.fromisoformat(a.day) if a.day else datetime.now(SAST).date()
    if a.cmd == "make":
        return make(day, voice=not a.no_voice)
    if a.cmd == "pulse":
        return pulse(day)
    if a.cmd == "post":
        return post(a.dry_run, tuple(a.force))
    if a.cmd == "collect":
        return collect()
    return report(a.dry_run)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(1)
