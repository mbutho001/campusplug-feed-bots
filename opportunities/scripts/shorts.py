#!/usr/bin/env python3
"""Campus Plug — turn the day's hero story fliers into a vertical Short (TikTok + YouTube).

    intro card (hook)  ->  one flier per hero, slow push-in  ->  outro card (search the app)

1080x1920, 30 fps, H.264 + AAC, about 15-25 seconds. A South African English voice
(edge-tts, en-ZA) reads the hook and the names; if the voice can't be made the video
still renders with a silent track, so a TTS outage never stops the post.

Runs in the 06:30 opp-digest job right after `meta_post.py --stage`, so the video is
made once and kept with the staged fliers in out/social-<date>/short-<date>.mp4.

    python3 shorts.py                 # today's Short from today's plan
    python3 shorts.py --day 2026-11-06 --no-voice
"""
import argparse
import html
import json
import shutil
import os
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

import channel_content
from email_digest import refresh_deadlines
from fliers import ASSETS, screenshot
from posts import APP_NAME

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
VOICE = "en-ZA-LeahNeural"
FPS = 30
INTRO, OUTRO, FLIER_MIN, FADE = 2.6, 3.2, 4.0, 0.35

CARD = """<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="{assets}/fonts.css"><link rel="stylesheet" href="{assets}/tokens.css">
<style>
html,body{{margin:0;width:1080px;height:1920px;overflow:hidden}}
body{{background:radial-gradient(circle at 50% 30%, var(--warm-800), var(--warm-950) 70%);
  color:var(--white);font-family:'Fredoka',sans-serif;display:flex;flex-direction:column;
  align-items:center;justify-content:center;text-align:center;padding:0 90px;box-sizing:border-box}}
img{{width:260px;height:260px;margin-bottom:70px;border-radius:56px}}
.kicker{{font-family:'Quicksand',sans-serif;font-weight:700;font-size:46px;letter-spacing:6px;
  color:var(--orange-400);text-transform:uppercase;margin-bottom:34px}}
h1{{font-size:112px;line-height:1.04;margin:0 0 46px;font-weight:700}}
h1 em{{font-style:normal;color:var(--orange-500)}}
p{{font-family:'Quicksand',sans-serif;font-size:50px;line-height:1.3;color:var(--gray-100);margin:0}}
.pill{{margin-top:80px;background:var(--orange-500);color:var(--black);font-weight:700;
  font-size:52px;border-radius:999px;padding:30px 64px}}
</style></head><body>{body}</body></html>"""


def card(kind, items, dest, workdir):
    logo = f'<img src="{(ASSETS / "campusplug-logo.png").as_uri()}">'
    if kind == "intro":
        n = len(items)
        word = "opportunity" if n == 1 else "opportunities"
        hook = channel_content.headline(items)
        sub = hook.split("— ", 1)[1].capitalize() if "— " in hook else "Apply before they close"
        body = (f'{logo}<div class="kicker">South Africa · {date.today():%d %b}</div>'
                f'<h1><em>{n}</em> {word}<br>closing soon</h1><p>{html.escape(sub)}</p>'
                '<div class="pill">Save this for later</div>')
    else:
        body = (f'{logo}<h1>Search the name on <em>{APP_NAME}</em></h1>'
                '<p>Requirements, closing dates and the apply link are all in the app.</p>'
                '<div class="pill">Free on Android · link in bio</div>')
    screenshot(CARD.format(assets=ASSETS.as_uri(), body=body), "story", dest, workdir)


def ensure_ffmpeg():
    """GitHub's ubuntu runner has no ffmpeg; install it on demand (passwordless sudo there)."""
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return
    if not (os.environ.get("CI") and shutil.which("apt-get")):
        raise RuntimeError("ffmpeg/ffprobe not installed")
    print("shorts: installing ffmpeg")
    env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
    subprocess.run(["sudo", "apt-get", "update", "-qq"], check=True, timeout=300, env=env, capture_output=True)
    subprocess.run(["sudo", "apt-get", "install", "-y", "-qq", "--no-install-recommends", "ffmpeg"],
                   check=True, timeout=600, env=env, capture_output=True)


def make_voice(text, dest):
    """MP3 of the voiceover, or None. Duration in seconds alongside."""
    exe = shutil.which("edge-tts")
    if not exe:
        return None, 0.0
    try:
        subprocess.run([exe, "--voice", VOICE, "--rate=+8%", "--text", text, "--write-media", str(dest)],
                       check=True, capture_output=True, timeout=90)
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                    "-of", "csv=p=0", str(dest)], capture_output=True, text=True,
                                   check=True).stdout.strip())
        return dest, dur
    except (subprocess.SubprocessError, ValueError) as exc:
        print(f"shorts: no voiceover ({exc!r}) - silent track")
        return None, 0.0


def render(images, durations, voice, dest):
    inputs, chains = [], []
    for n, (img, d) in enumerate(zip(images, durations)):
        frames = int(d * FPS)
        inputs += ["-loop", "1", "-t", f"{d:.2f}", "-i", str(img)]
        # Upscale first so the slow push-in doesn't shimmer.
        chains.append(f"[{n}:v]scale=1620:2880,zoompan=z='min(zoom+0.0007,1.07)':d={frames}"
                      f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps={FPS},"
                      f"setsar=1,format=yuv420p[v{n}]")
    # Cross-fade the clips into one stream.
    last, offset = "v0", 0.0
    for n in range(1, len(images)):
        offset += durations[n - 1] - FADE
        chains.append(f"[{last}][v{n}]xfade=transition=fade:duration={FADE}:offset={offset:.2f}[x{n}]")
        last = f"x{n}"
    total = sum(durations) - FADE * (len(images) - 1)
    a = len(images)
    if voice:
        inputs += ["-i", str(voice)]
        chains.append(f"[{a}:a]adelay=500|500,apad,atrim=0:{total:.2f},afade=t=out:st={total - 0.6:.2f}:d=0.6[a]")
    else:
        inputs += ["-f", "lavfi", "-t", f"{total:.2f}", "-i", "anullsrc=r=44100:cl=stereo"]
        chains.append(f"[{a}:a]anull[a]")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(chains),
           "-map", f"[{last}]", "-map", "[a]", "-c:v", "libx264", "-preset", "medium", "-crf", "21",
           "-pix_fmt", "yuv420p", "-r", str(FPS), "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
           "-movflags", "+faststart", "-t", f"{total:.2f}", str(dest)]
    subprocess.run(cmd, check=True, timeout=600)
    return total


def build(day, voice=True):
    ensure_ffmpeg()
    plan_path = OUT / f"social-plan-{day}.json"
    if not plan_path.exists():
        print(f"shorts: no social plan for {day}")
        return None
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    slot = next((s for s in plan["slots"] if "YouTube Shorts" in s["platforms"] or "TikTok" in s["platforms"]), None)
    if not slot:
        print("shorts: no TikTok/YouTube slot in the plan")
        return None
    folder = OUT / f"social-{day}"
    folder.mkdir(parents=True, exist_ok=True)
    fliers = []
    for f in slot["files"]:
        for src in (folder / Path(f).name, OUT / f"fliers-{day}" / Path(f).name, ROOT / f):
            if src.exists():
                fliers.append(src)
                break
    if not fliers:
        print("shorts: none of the story fliers exist")
        return None
    feed = json.loads((OUT / "latest.json").read_text(encoding="utf-8"))
    by_id = {i["id"]: i for i in refresh_deadlines(feed, lambda *_: None)}
    items = [by_id[i] for i in slot["listings"] if i in by_id] or [{"id": i} for i in slot["listings"]]

    dest = folder / f"short-{day}.mp4"
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        intro, outro = tmp / "intro.png", tmp / "outro.png"
        card("intro", items, intro, tmp)
        card("outro", items, outro, tmp)
        vo, vo_len = make_voice(channel_content.voiceover(items), tmp / "vo.mp3") if voice else (None, 0.0)
        # Stretch the flier clips so the voice always finishes before the outro ends.
        per = max(FLIER_MIN, (vo_len + 1.0 - INTRO - OUTRO + FADE * (len(fliers) + 1)) / len(fliers))
        durations = [INTRO] + [per] * len(fliers) + [OUTRO]
        total = render([intro, *fliers, outro], durations, vo, dest)
    print(f"shorts: {dest.name} {total:.1f}s, {len(fliers)} fliers, voice {'yes' if vo else 'no'}")
    return dest


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--day", default=date.today().isoformat())
    ap.add_argument("--no-voice", action="store_true")
    args = ap.parse_args()
    build(args.day, voice=not args.no_voice)
    return 0


if __name__ == "__main__":
    # A failed video must never stop the digest mail that runs after it.
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"shorts FAILED: {exc!r}")
        sys.exit(0)
