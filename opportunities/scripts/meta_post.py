#!/usr/bin/env python3
"""Campus Plug — post the day's opportunity fliers to Facebook, Instagram and Threads.

social_plan.py (06:30) picks the day's hero fliers and gives every slot a time, the
flier files and a caption. This posts the Meta slots of that plan through the Graph API:

    19:00          Instagram feed + Facebook Page carousel of the heroes  -> FB, IG, Threads
    Mon 12:00      "closing this week" carousel                           -> FB, IG, Threads

Fliers are rendered fresh each morning and are not kept between cron ticks, so
`--stage` (run right after social_plan.py) copies the files the plan uses into
out/social-<date>/, which the workflow keeps in the state repo.

Images go to Facebook first (unpublished photos, then one post with them attached).
Instagram and Threads need public image URLs, so they reuse the Facebook CDN URL of
each uploaded photo. A slot is posted once per platform: state/meta-posted.json.
A slot is skipped after SLOT_GRACE_HOURS, so a dead morning never posts at midnight.

Secrets (environment, set as GitHub secrets — never in this public repo):
    META_PAGE_TOKEN   never-expiring Page access token for the CampusPlug Page
                      (pages_manage_posts, pages_read_engagement,
                       instagram_basic, instagram_content_publish)
    THREADS_TOKEN     optional; long-lived Threads token (threads_basic,
                      threads_content_publish). Refreshed weekly into
                      state/threads-token.json, which then wins over the secret.
    META_POST_DRY_RUN=1   log what would be posted, post nothing

    python3 meta_post.py --stage          # after social_plan.py: copy today's plan files
    python3 meta_post.py                  # post whatever slot is due
    python3 meta_post.py --dry-run        # show what is due, post nothing
    python3 meta_post.py --check          # verify the tokens and print the connected accounts
"""
import argparse
import json
import mimetypes
import os
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

SAST = ZoneInfo("Africa/Johannesburg")
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
STATE = ROOT / "state" / "meta-posted.json"
THREADS_STATE = ROOT / "state" / "threads-token.json"
GRAPH = "https://graph.facebook.com/v23.0"
THREADS = "https://graph.threads.net/v1.0"
META_PLATFORMS = ("Instagram feed", "Facebook Page")
SLOT_GRACE_HOURS = 3
THREADS_LIMIT = 500
IG_CAROUSEL_MAX = 10
THREADS_CAROUSEL_MAX = 20
THREADS_REFRESH_DAYS = 7


class GraphError(RuntimeError):
    pass


def call(method, url, params=None, files=None, timeout=60):
    """One Graph API call. Raises GraphError with Meta's message on failure."""
    params = {k: v for k, v in (params or {}).items() if v is not None}
    if method == "GET":
        req = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}")
    elif files:
        boundary = uuid.uuid4().hex
        body = bytearray()
        for k, v in params.items():
            body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
        for k, path in files.items():
            ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
            body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; "
                     f"filename=\"{Path(path).name}\"\r\nContent-Type: {ctype}\r\n\r\n").encode()
            body += Path(path).read_bytes() + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        req = urllib.request.Request(url, data=bytes(body), method="POST",
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    else:
        req = urllib.request.Request(url, data=urllib.parse.urlencode(params).encode(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            err = json.loads(exc.read()).get("error", {})
            msg = f"{err.get('type')} {err.get('code')}: {err.get('message')}"
        except Exception:  # noqa: BLE001
            msg = str(exc)
        # Never echo the token: the URL of a GET carries it.
        raise GraphError(f"{method} {url.split('?')[0]} -> {msg}") from None


# ---------------------------------------------------------------- tokens

def page_token():
    return os.environ.get("META_PAGE_TOKEN", "").strip()


def threads_token():
    """The newest Threads token: the refreshed one in state, else the secret. Refreshes weekly."""
    tok, saved = os.environ.get("THREADS_TOKEN", "").strip(), None
    if THREADS_STATE.exists():
        try:
            saved = json.loads(THREADS_STATE.read_text())
            tok = saved.get("token") or tok
        except ValueError:
            pass
    if not tok:
        return ""
    last = (saved or {}).get("refreshed")
    if not last or (date.today() - date.fromisoformat(last)).days >= THREADS_REFRESH_DAYS:
        try:
            r = call("GET", "https://graph.threads.net/refresh_access_token",
                     {"grant_type": "th_refresh_token", "access_token": tok})
            tok = r.get("access_token") or tok
            THREADS_STATE.parent.mkdir(parents=True, exist_ok=True)
            THREADS_STATE.write_text(json.dumps({"token": tok, "refreshed": date.today().isoformat()}))
            os.chmod(THREADS_STATE, 0o600)
            print("threads: token refreshed")
        except GraphError as exc:
            print(f"threads: token refresh failed ({exc}); using the current token")
    return tok


def page_info(token):
    """(page id, page name, instagram business account id or None) for a Page token."""
    me = call("GET", f"{GRAPH}/me", {"fields": "id,name,instagram_business_account{id,username}",
                                    "access_token": token})
    ig = me.get("instagram_business_account") or {}
    return me["id"], me.get("name"), ig.get("id"), ig.get("username")


# ---------------------------------------------------------------- posting

def fb_post(page_id, token, files, caption):
    """Upload the fliers unpublished, then one Page post with all of them. Returns (post id, CDN urls)."""
    photo_ids, urls = [], []
    for f in files:
        r = call("POST", f"{GRAPH}/{page_id}/photos",
                 {"published": "false", "access_token": token}, files={"source": f}, timeout=120)
        photo_ids.append(r["id"])
        img = call("GET", f"{GRAPH}/{r['id']}", {"fields": "images", "access_token": token})
        best = max(img.get("images", []), key=lambda i: i.get("width", 0), default={})
        urls.append(best.get("source"))
    params = {"message": caption, "access_token": token}
    for n, pid in enumerate(photo_ids):
        params[f"attached_media[{n}]"] = json.dumps({"media_fbid": pid})
    post = call("POST", f"{GRAPH}/{page_id}/feed", params)
    return post.get("id"), urls


def wait_ready(base, container, token, field="status_code", tries=20):
    for _ in range(tries):
        r = call("GET", f"{base}/{container}", {"fields": field, "access_token": token})
        status = r.get(field)
        if status in ("FINISHED", "PUBLISHED"):
            return
        if status in ("ERROR", "EXPIRED"):
            raise GraphError(f"container {container} {status}")
        time.sleep(3)
    raise GraphError(f"container {container} not ready after {tries * 3}s")


def ig_post(ig_id, token, urls, caption):
    urls = urls[:IG_CAROUSEL_MAX]
    if len(urls) == 1:
        c = call("POST", f"{GRAPH}/{ig_id}/media", {"image_url": urls[0], "caption": caption,
                                                    "access_token": token})["id"]
    else:
        kids = [call("POST", f"{GRAPH}/{ig_id}/media", {"image_url": u, "is_carousel_item": "true",
                                                        "access_token": token})["id"] for u in urls]
        for k in kids:
            wait_ready(GRAPH, k, token)
        c = call("POST", f"{GRAPH}/{ig_id}/media", {"media_type": "CAROUSEL", "children": ",".join(kids),
                                                    "caption": caption, "access_token": token})["id"]
    wait_ready(GRAPH, c, token)
    return call("POST", f"{GRAPH}/{ig_id}/media_publish", {"creation_id": c, "access_token": token}).get("id")


def threads_caption(caption):
    if len(caption) <= THREADS_LIMIT:
        return caption
    # Drop the hashtag line first, then cut at a line break.
    lines = [l for l in caption.split("\n") if not l.startswith("#")]
    text = "\n".join(lines)
    while len(text) > THREADS_LIMIT and "\n" in text:
        text = text.rsplit("\n", 1)[0]
    return text[:THREADS_LIMIT]


def threads_post(token, urls, caption):
    urls, text = urls[:THREADS_CAROUSEL_MAX], threads_caption(caption)
    if len(urls) == 1:
        c = call("POST", f"{THREADS}/me/threads", {"media_type": "IMAGE", "image_url": urls[0],
                                                   "text": text, "access_token": token})["id"]
    else:
        kids = [call("POST", f"{THREADS}/me/threads", {"media_type": "IMAGE", "image_url": u,
                                                       "is_carousel_item": "true", "access_token": token})["id"]
                for u in urls]
        for k in kids:
            wait_ready(THREADS, k, token, field="status")
        c = call("POST", f"{THREADS}/me/threads", {"media_type": "CAROUSEL", "children": ",".join(kids),
                                                   "text": text, "access_token": token})["id"]
    wait_ready(THREADS, c, token, field="status")
    return call("POST", f"{THREADS}/me/threads_publish", {"creation_id": c, "access_token": token}).get("id")


# ---------------------------------------------------------------- plan

def load_plan(day):
    p = OUT / f"social-plan-{day}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def meta_slots(plan):
    return [(n, s) for n, s in enumerate(plan["slots"])
            if any(p in s["platforms"] for p in META_PLATFORMS)]


def staged(day, f):
    return OUT / f"social-{day}" / Path(f).name


def stage(day):
    plan = load_plan(day)
    if not plan:
        print(f"no social plan for {day} - nothing to stage")
        return 0
    folder = OUT / f"social-{day}"
    folder.mkdir(parents=True, exist_ok=True)
    n = 0
    for _, s in meta_slots(plan):
        for f in s["files"]:
            src = Path(f) if Path(f).is_absolute() else ROOT / f
            if not src.exists():
                src = OUT / f"fliers-{day}" / Path(f).name
            if src.exists():
                shutil.copy2(src, folder / src.name)
                n += 1
            else:
                print(f"stage: missing flier {f}")
    print(f"staged {n} fliers for the Meta slots of {day}")
    return 0


def due(plan, now):
    """Meta slots whose time has passed today and are still inside the grace window."""
    out = []
    for n, s in meta_slots(plan):
        h, m = map(int, s["time"].split(":"))
        at = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if at <= now < at + timedelta(hours=SLOT_GRACE_HOURS):
            out.append((n, s))
    return out


def run(dry):
    now = datetime.now(SAST)
    day = now.date().isoformat()
    plan = load_plan(day)
    if not plan:
        print(f"no social plan for {day}")
        return 0
    slots = due(plan, now)
    if not slots:
        return 0
    posted = json.loads(STATE.read_text()) if STATE.exists() else {}
    ptok, ttok = page_token(), threads_token()
    if not ptok and not dry:
        print("META_PAGE_TOKEN not set - Meta posting is off")
        return 0
    page_id = ig_id = None
    failures = 0
    for n, s in slots:
        key = f"{day}#{n}"
        done = posted.setdefault(key, {})
        files = [staged(day, f) for f in s["files"]]
        files = [f for f in files if f.exists()]
        if not files:
            print(f"{key}: no staged fliers - skipped")
            continue
        caption = s["caption"]
        if dry:
            print(f"[dry-run] {s['time']} {len(files)} fliers -> FB/IG/Threads (done: {sorted(done)})\n{caption}\n")
            continue
        if page_id is None:
            page_id, _, ig_id, _ = page_info(ptok)
        urls = done.get("urls")
        try:
            if "facebook" not in done:
                # Facebook's caption link is clickable: swap the "link in bio" line for the tracked link.
                from fliers import tracked_play_url
                fb_caption = caption.replace("— link in bio.", "→ " + tracked_play_url("facebook", "social"))
                done["facebook"], urls = fb_post(page_id, ptok, files, fb_caption)
                done["urls"] = urls
                print(f"{key}: facebook {done['facebook']}")
        except GraphError as exc:
            failures += 1
            print(f"{key}: facebook FAILED {exc}")
        if urls and ig_id and "instagram" not in done:
            try:
                done["instagram"] = ig_post(ig_id, ptok, urls, caption)
                print(f"{key}: instagram {done['instagram']}")
            except GraphError as exc:
                failures += 1
                print(f"{key}: instagram FAILED {exc}")
        if urls and ttok and "threads" not in done:
            try:
                done["threads"] = threads_post(ttok, urls, caption)
                print(f"{key}: threads {done['threads']}")
            except GraphError as exc:
                failures += 1
                print(f"{key}: threads FAILED {exc}")
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(posted, indent=2))
    # Keep the file small.
    cutoff = (now.date() - timedelta(days=30)).isoformat()
    posted = {k: v for k, v in posted.items() if k.split("#")[0] >= cutoff}
    if not dry:
        STATE.write_text(json.dumps(posted, indent=2))
    return 1 if failures else 0


def check():
    ptok, ttok = page_token(), threads_token()
    ok = True
    if ptok:
        try:
            pid, name, ig, igname = page_info(ptok)
            print(f"facebook page: {name} ({pid})")
            print(f"instagram: @{igname} ({ig})" if ig else "instagram: NOT linked to this Page")
            dbg = call("GET", f"{GRAPH}/me/permissions", {"access_token": ptok})
            print("permissions:", ", ".join(p["permission"] for p in dbg.get("data", []) if p.get("status") == "granted") or "(page token)")
        except GraphError as exc:
            ok = False
            print(f"facebook: {exc}")
    else:
        ok = False
        print("META_PAGE_TOKEN not set")
    if ttok:
        try:
            me = call("GET", f"{THREADS}/me", {"fields": "id,username", "access_token": ttok})
            print(f"threads: @{me.get('username')} ({me.get('id')})")
        except GraphError as exc:
            ok = False
            print(f"threads: {exc}")
    else:
        print("THREADS_TOKEN not set - Threads posting is off")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return check()
    if args.stage:
        return stage(date.today().isoformat())
    return run(args.dry_run or os.environ.get("META_POST_DRY_RUN") == "1")


if __name__ == "__main__":
    sys.exit(main())
