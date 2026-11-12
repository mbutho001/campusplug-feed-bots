#!/usr/bin/env python3
"""Campus Plug — post the day's opportunities to the WhatsApp Channel, TikTok and YouTube.

social_plan.py (06:30) gives each platform a slot; shorts.py makes the vertical video.
Every tick this posts the slots that are due, once each (state/channel-posted.json):

    07:30  WhatsApp Channel  square fliers, the roundup as the first one's caption
                             (Node + Baileys linked device: scripts/wa/wa_post.mjs)
    12:30  TikTok            the Short, Content Posting API (direct post, file upload)
    17:00  YouTube Shorts    the Short, YouTube Data API v3 (resumable upload)

A slot is skipped after SLOT_GRACE_HOURS so a dead morning never posts at night, and a
platform without its secrets is simply off. One platform failing never stops the others.

Secrets (GitHub secrets, never in this public repo):
    WA_AUTH_KEY, WA_CHANNEL         key for state/wa-auth.enc (made by wa/wa_link.mjs) and the
                                    channel invite code or ...@newsletter id
    TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET, TIKTOK_REFRESH_TOKEN
                                    (tiktok_token.py; the refresh token rotates into
                                     state/tiktok-token.json, which then wins)
    YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN   (yt_token.py)
    CHANNEL_POST_DRY_RUN=1          log what would be posted, post nothing

    python3 channel_post.py              # post whatever is due
    python3 channel_post.py --dry-run    # show what is due, post nothing
    python3 channel_post.py --check      # verify the tokens, print the accounts
    python3 channel_post.py --only youtube --force   # post that slot now (testing)
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

SAST = ZoneInfo("Africa/Johannesburg")
SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
OUT = ROOT / "out"
STATE = ROOT / "state" / "channel-posted.json"
TIKTOK_STATE = ROOT / "state" / "tiktok-token.json"
SLOT_GRACE_HOURS = 3
PLATFORM_OF = {"WhatsApp Channel": "whatsapp", "TikTok": "tiktok", "YouTube Shorts": "youtube",
               "X": "x", "LinkedIn": "linkedin"}
MULTI_SLOT = {"x", "linkedin"}   # several slots a day: the state key carries the slot time


class PostError(RuntimeError):
    pass


def http(method, url, data=None, headers=None, body=None, timeout=120, want_headers=False):
    headers = dict(headers or {})
    if data is not None:
        body = json.dumps(data).encode()
        headers.setdefault("Content-Type", "application/json; charset=UTF-8")
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            parsed = json.loads(raw) if raw and raw[:1] in b"{[" else {}
            return (parsed, r.headers) if want_headers else parsed
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:400].decode("utf-8", "replace")
        raise PostError(f"{method} {url.split('?')[0]} -> HTTP {exc.code}: {detail}") from None


def form(url, fields):
    body = urllib.parse.urlencode(fields).encode()
    return http("POST", url, body=body, headers={"Content-Type": "application/x-www-form-urlencoded"})


def env(name):
    return os.environ.get(name, "").strip()


# ---------------------------------------------------------------- YouTube

def yt_access():
    if not (env("YT_CLIENT_ID") and env("YT_CLIENT_SECRET") and env("YT_REFRESH_TOKEN")):
        return None
    r = form("https://oauth2.googleapis.com/token", {
        "client_id": env("YT_CLIENT_ID"), "client_secret": env("YT_CLIENT_SECRET"),
        "refresh_token": env("YT_REFRESH_TOKEN"), "grant_type": "refresh_token"})
    return r["access_token"]


def youtube_post(slot, video):
    tok = yt_access()
    if not tok:
        return None, "YT_* secrets not set - YouTube posting is off"
    meta = {"snippet": {"title": slot["title"], "description": slot["caption"], "tags": slot.get("tags", []),
                        "categoryId": "27", "defaultLanguage": "en", "defaultAudioLanguage": "en"},
            "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False,
                       "embeddable": True}}
    size = video.stat().st_size
    _, hdrs = http("POST", "https://www.googleapis.com/upload/youtube/v3/videos"
                   "?uploadType=resumable&part=snippet,status", data=meta, want_headers=True,
                   headers={"Authorization": f"Bearer {tok}", "X-Upload-Content-Type": "video/mp4",
                            "X-Upload-Content-Length": str(size)})
    up = http("PUT", hdrs["Location"], body=video.read_bytes(), timeout=600,
              headers={"Authorization": f"Bearer {tok}", "Content-Type": "video/mp4"})
    vid, privacy = up.get("id"), (up.get("status") or {}).get("privacyStatus")
    note = "" if privacy == "public" else \
        f" (YouTube set it to {privacy}: an unaudited API project can only upload private videos)"
    return vid, f"https://youtube.com/shorts/{vid}{note}"


# ---------------------------------------------------------------- TikTok

TT = "https://open.tiktokapis.com/v2"


def tiktok_access():
    key, secret = env("TIKTOK_CLIENT_KEY"), env("TIKTOK_CLIENT_SECRET")
    refresh = env("TIKTOK_REFRESH_TOKEN")
    if TIKTOK_STATE.exists():
        try:
            refresh = json.loads(TIKTOK_STATE.read_text()).get("refresh_token") or refresh
        except ValueError:
            pass
    if not (key and secret and refresh):
        return None
    r = form(f"{TT}/oauth/token/", {"client_key": key, "client_secret": secret,
                                     "grant_type": "refresh_token", "refresh_token": refresh})
    if "access_token" not in r:
        raise PostError(f"tiktok token refresh: {r.get('error_description') or r}")
    # TikTok rotates the refresh token; keep the newest one.
    TIKTOK_STATE.parent.mkdir(parents=True, exist_ok=True)
    TIKTOK_STATE.write_text(json.dumps({"refresh_token": r.get("refresh_token", refresh),
                                        "refreshed": date.today().isoformat()}))
    os.chmod(TIKTOK_STATE, 0o600)
    return r["access_token"]


def tt_call(path, tok, data):
    r = http("POST", f"{TT}{path}", data=data, headers={"Authorization": f"Bearer {tok}"})
    err = r.get("error") or {}
    if err.get("code") not in (None, "ok"):
        raise PostError(f"tiktok {path}: {err.get('code')} {err.get('message')}")
    return r.get("data") or {}


def tiktok_post(slot, video):
    tok = tiktok_access()
    if not tok:
        return None, "TIKTOK_* secrets not set - TikTok posting is off"
    info = tt_call("/post/publish/creator_info/query/", tok, {})
    options = info.get("privacy_level_options") or ["SELF_ONLY"]
    privacy = "PUBLIC_TO_EVERYONE" if "PUBLIC_TO_EVERYONE" in options else options[0]
    size = video.stat().st_size

    def init_post(level):
        return tt_call("/post/publish/video/init/", tok, {
            "post_info": {"title": slot["caption"], "privacy_level": level,
                          "disable_duet": False, "disable_stitch": False, "disable_comment": False,
                          "video_cover_timestamp_ms": 1200, "brand_organic_toggle": True},
            "source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": size,
                            "total_chunk_count": 1}})
    inbox = False
    try:
        init = init_post(privacy)
    except PostError as exc:
        # An unaudited app can direct-post only to a PRIVATE account. Until the Direct Post audit
        # passes, upload to the creator's TikTok inbox instead: a draft they publish with one tap.
        if "unaudited_client" not in str(exc):
            raise
        inbox = True
        init = tt_call("/post/publish/inbox/video/init/", tok, {
            "source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": size,
                            "total_chunk_count": 1}})
    http("PUT", init["upload_url"], body=video.read_bytes(), timeout=600,
         headers={"Content-Type": "video/mp4", "Content-Range": f"bytes 0-{size - 1}/{size}"})
    pid = init["publish_id"]
    for _ in range(40):
        st = tt_call("/post/publish/status/fetch/", tok, {"publish_id": pid})
        status = st.get("status")
        if status == "PUBLISH_COMPLETE" or (inbox and status == "SEND_TO_USER_INBOX"):
            break
        if status == "FAILED":
            raise PostError(f"tiktok publish failed: {st.get('fail_reason')}")
        time.sleep(6)
    if inbox:
        return pid, (f"@{info.get('creator_username', '?')} as an INBOX DRAFT (app not audited): "
                     "open TikTok, tap the notification, paste the caption from the digest mail, Post")
    note = "" if privacy == "PUBLIC_TO_EVERYONE" else \
        f" (posted as {privacy}: TikTok allows only private posts until the app passes its audit)"
    return pid, f"@{info.get('creator_username', '?')}{note}"


# ---------------------------------------------------------------- WhatsApp Channel

def whatsapp_post(slot, files):
    if not (env("WA_AUTH_KEY") and env("WA_CHANNEL")):
        return None, "WA_AUTH_KEY / WA_CHANNEL not set - WhatsApp Channel posting is off"
    text = ROOT / "state" / ".wa-caption.txt"
    text.write_text(slot["caption"], encoding="utf-8")
    try:
        proc = subprocess.run(["node", str(SCRIPTS / "wa" / "wa_post.mjs"), "--caption-file", str(text),
                               *map(str, files)], capture_output=True, text=True, timeout=240,
                              cwd=SCRIPTS / "wa")
    finally:
        text.unlink(missing_ok=True)
    out = (proc.stdout + proc.stderr).strip()
    if proc.returncode != 0:
        raise PostError(f"whatsapp: {out[-500:]}")
    # Baileys prints libsignal stack traces to stderr; the result is the "posted <id> to ..." line.
    done = [l for l in proc.stdout.splitlines() if l.startswith("posted ")]
    if not done:
        raise PostError(f"whatsapp: no confirmation line: {out[-500:]}")
    return done[-1].removeprefix("posted ").split(" to ")[0].strip(), "to the WhatsApp Channel"


# ---------------------------------------------------------------- plan

def slot_files(day, slot):
    folder = OUT / f"social-{day}"
    found = []
    for f in slot["files"]:
        for src in (folder / Path(f).name, OUT / f"fliers-{day}" / Path(f).name):
            if src.exists():
                found.append(src)
                break
    return found


def channel_slots(plan):
    for s in plan["slots"]:
        for p in s["platforms"]:
            if p in PLATFORM_OF:
                yield PLATFORM_OF[p], s


def run(dry, only=None, force=False):
    now = datetime.now(SAST)
    day = now.date().isoformat()
    path = OUT / f"social-plan-{day}.json"
    if not path.exists():
        print(f"no social plan for {day}")
        return 0
    plan = json.loads(path.read_text(encoding="utf-8"))
    posted = json.loads(STATE.read_text()) if STATE.exists() else {}
    failures = 0
    rebuilt = set()
    for platform, s in channel_slots(plan):
        if only and platform not in only:
            continue
        key = f"{day}#{platform}" + (f"@{s['time']}" if platform in MULTI_SLOT else "")
        if key in posted and not (force and "error" in posted[key]):
            continue
        h, m = map(int, s["time"].split(":"))
        at = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if not force and not (at <= now < at + timedelta(hours=SLOT_GRACE_HOURS)):
            continue
        video = OUT / f"social-{day}" / f"short-{day}.mp4"
        files = slot_files(day, s)
        if platform in ("tiktok", "youtube") and not video.exists() and not dry and day not in rebuilt:
            # The 06:30 render failed (or was skipped); try once more now that a video slot is due.
            rebuilt.add(day)
            try:
                import shorts
                shorts.build(day)
            except Exception as exc:  # noqa: BLE001
                print(f"{key}: Short re-render failed: {exc!r}")
        if platform in ("tiktok", "youtube") and not video.exists():
            print(f"{key}: no Short for today - skipped")
            continue
        if platform in ("whatsapp", "x", "linkedin") and not files:
            print(f"{key}: no staged fliers - skipped")
            continue
        if dry:
            print(f"[dry-run] {s['time']} {platform}: {video.name if platform != 'whatsapp' else len(files)}\n"
                  f"{s.get('title', '')}\n{s['caption']}\n")
            continue
        try:
            import pro_post
            poster = {"whatsapp": lambda: whatsapp_post(s, files),
                      "x": lambda: pro_post.x_post(s["caption"], files[:1]),
                      "linkedin": lambda: pro_post.linkedin_post(s["caption"], files),
                      "tiktok": lambda: tiktok_post(s, video),
                      "youtube": lambda: youtube_post(s, video)}[platform]
            pid, note = poster()
            if pid is None:
                print(f"{key}: {note}")
                continue
            posted[key] = {"id": pid, "at": now.isoformat(timespec="minutes")}
            print(f"{key}: posted {pid} {note}")
        except Exception as exc:  # noqa: BLE001 - one platform failing never stops the others
            failures += 1
            # Mark attempted so a broken post never retries every 10 minutes all afternoon.
            posted[key] = {"error": str(exc)[:300], "at": now.isoformat(timespec="minutes")}
            print(f"{key}: FAILED {exc}")
    cutoff = (date.today() - timedelta(days=14)).isoformat()
    posted = {k: v for k, v in posted.items() if k.split("#")[0] >= cutoff}
    if not dry:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(posted, indent=2))
    return 1 if failures else 0


def check():
    ok = True
    for name, fn in (("youtube", yt_access), ("tiktok", tiktok_access)):
        try:
            tok = fn()
            if not tok:
                print(f"{name}: secrets not set")
                continue
            if name == "youtube":
                r = http("GET", "https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true",
                         headers={"Authorization": f"Bearer {tok}"})
                print("youtube:", ", ".join(c["snippet"]["title"] for c in r.get("items", [])) or "no channel")
            else:
                info = tt_call("/post/publish/creator_info/query/", tok, {})
                print(f"tiktok: @{info.get('creator_username')} privacy options {info.get('privacy_level_options')}")
        except (PostError, urllib.error.URLError, KeyError) as exc:
            ok = False
            print(f"{name}: {exc}")
    import pro_post
    for fn in (pro_post.x_check, pro_post.linkedin_check):
        try:
            print(fn())
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"{fn.__name__}: {exc}")
    if env("WA_AUTH_KEY") and env("WA_CHANNEL"):
        proc = subprocess.run(["node", str(SCRIPTS / "wa" / "wa_post.mjs"), "--check"], capture_output=True,
                              text=True, timeout=120, cwd=SCRIPTS / "wa")
        print("whatsapp:", (proc.stdout + proc.stderr).strip()[-300:])
        ok = ok and proc.returncode == 0
    else:
        print("whatsapp: secrets not set")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--only", nargs="*", choices=sorted(set(PLATFORM_OF.values())))
    ap.add_argument("--force", action="store_true", help="post the --only slots now, whatever the time")
    args = ap.parse_args()
    if args.check:
        return check()
    return run(args.dry_run or env("CHANNEL_POST_DRY_RUN") == "1", args.only, args.force)


if __name__ == "__main__":
    sys.exit(main())
