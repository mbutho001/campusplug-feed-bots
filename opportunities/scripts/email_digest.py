#!/usr/bin/env python3
"""Email the collected opportunities as a daily digest.

Reads out/latest.json (and the publishing pack if posts.py has run) and sends
one HTML mail through the existing GoDaddy mailbox, as the campusplug alias.

    python3 email_digest.py --dry-run    # render logs/preview.html, send nothing
    python3 email_digest.py              # render and send
    python3 email_digest.py --to a@b.c   # override the recipient

Deadline pressure is the point of the mail, so it leads with whatever closes
within a week. Sources that failed to report get their own box - a quiet source
must never read as a quiet day.
"""
import argparse
import os
import html
import json
import smtplib
import ssl
import sys
from datetime import date, datetime
from email.message import EmailMessage
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(Path.home() / "claude-code" / "qconnec" / "qconnec-mail"))
from cron_sender import send as deliver_mail  # noqa: E402  Gmail sender shared by all cron emails

DEFAULT_TO = os.environ.get("DIGEST_RECIPIENTS", "")  # set from a GitHub secret; kept out of this public repo
FROM_ADDRESS = "campusplug@qconnec.co.za"
FROM_NAME = "Campus Plug Opportunities"

URGENT_DAYS = 7
# Gmail refuses a message over 25 MB, and base64 inflates attachments by a
# third, so each mail carries at most this many bytes of flier images.
FLIER_BATCH_BYTES = 16 * 1024 * 1024
CATEGORY_EMOJI = {"bursary": "🎓", "internship": "💼", "learnership": "🛠️",
                  "apprenticeship": "🔧", "in-service training": "📋",
                  "graduate programme": "🚀"}


def esc(text):
    return html.escape(str(text), quote=True)


def pretty(iso):
    if not iso:
        return None
    return date.fromisoformat(iso).strftime("%-d %B %Y")


def deadline_cell(item):
    left, close = item.get("days_left"), pretty(item.get("closing_date"))
    if close is None:
        return ('<span style="color:#9a6a00">closing date not stated — '
                'check the listing</span>')
    if left is None:
        return esc(close)
    if left <= 0:
        return f'<strong style="color:#c0392b">CLOSES TODAY</strong> · {esc(close)}'
    unit = "day" if left == 1 else "days"
    if left <= 3:
        return f'<strong style="color:#c0392b">{left} {unit} left</strong> · {esc(close)}'
    if left <= URGENT_DAYS:
        return f'<strong style="color:#d35400">{left} {unit} left</strong> · {esc(close)}'
    return f"{left} {unit} · {esc(close)}"


def row(item):
    meta = []
    if item.get("employer"):
        meta.append(esc(item["employer"]))
    if item.get("province"):
        meta.append(esc(item["province"]))
    if item.get("fields"):
        meta.append(esc(", ".join(item["fields"][:3])))
    if item.get("matric_friendly"):
        meta.append('<span style="color:#0a7d3f">matric welcome</span>')
    sources = item.get("source", "")
    if item.get("also_on"):
        sources += " + " + ", ".join(item["also_on"])
    return f"""
    <tr>
      <td style="padding:10px 8px;border-bottom:1px solid #eee;vertical-align:top">
        <a href="{esc(item['url'])}" style="color:#1a4fd6;text-decoration:none;font-weight:600">
          {esc(item['title'])}</a>
        <div style="font-size:12px;color:#666;margin-top:3px">{' · '.join(meta)}</div>
        <div style="font-size:11px;color:#999;margin-top:2px">via {esc(sources)}</div>
      </td>
      <td style="padding:10px 8px;border-bottom:1px solid #eee;white-space:nowrap;
                 font-size:12px;vertical-align:top">{deadline_cell(item)}</td>
    </tr>"""


def render_html(feed, pack, when):
    items = feed["opportunities"]
    urgent = [i for i in items
              if i.get("days_left") is not None and 0 <= i["days_left"] <= URGENT_DAYS]
    rest = [i for i in items if i not in urgent]

    by_cat = {}
    for i in rest:
        by_cat.setdefault(i.get("primary_category") or "other", []).append(i)

    parts = [f"""<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,sans-serif;
                 max-width:680px;margin:0 auto;color:#1a1a1a">
      <h1 style="font-size:20px;margin:0 0 4px">Campus Plug — opportunities for {esc(when)}</h1>
      <p style="color:#666;font-size:13px;margin:0 0 18px">
        {len(items)} open opportunities ·
        {' · '.join(f'{n} {esc(c)}' for c, n in feed.get('by_category', {}).items())}
      </p>"""]

    if urgent:
        parts.append(f"""
      <h2 style="font-size:15px;background:#fff4f0;border-left:3px solid #d35400;
                 padding:8px 10px;margin:18px 0 6px">
        ⏳ Closing within {URGENT_DAYS} days ({len(urgent)}) — post these first</h2>
      <table style="width:100%;border-collapse:collapse;font-size:14px">
        {''.join(row(i) for i in urgent)}
      </table>""")

    for cat, group in sorted(by_cat.items(), key=lambda kv: -len(kv[1])):
        emoji = CATEGORY_EMOJI.get(cat, "📌")
        parts.append(f"""
      <h2 style="font-size:15px;border-bottom:2px solid #1a1a1a;padding-bottom:4px;
                 margin:22px 0 4px">{emoji} {esc(cat.title())} ({len(group)})</h2>
      <table style="width:100%;border-collapse:collapse;font-size:14px">
        {''.join(row(i) for i in group)}
      </table>""")

    if pack:
        caption = pack.get("roundup", {}).get("whatsapp", {}).get("text", "")
        if caption:
            parts.append(f"""
      <h2 style="font-size:15px;margin:24px 0 6px">📣 Ready-to-post roundup</h2>
      <pre style="background:#f6f6f6;padding:12px;border-radius:6px;font-size:12px;
                  white-space:pre-wrap;font-family:ui-monospace,Menlo,monospace">{esc(caption)}</pre>
      <p style="font-size:12px;color:#666">
        Per-platform captions are in the publishing pack on the machine.</p>""")

    missing = feed.get("sources_not_reporting", [])
    manual = feed.get("sources_needing_manual_fetch", [])
    if missing or manual:
        lines = [f"<li><strong>{esc(m['name'])}</strong> — {esc(m['reason'])}</li>"
                 for m in missing + manual]
        parts.append(f"""
      <div style="background:#fffbe6;border:1px solid #ffe08a;border-radius:6px;
                  padding:10px 14px;margin:24px 0;font-size:13px">
        <strong>Sources not reporting this run</strong>
        <ul style="margin:6px 0 0;padding-left:18px">{''.join(lines)}</ul>
        <p style="margin:6px 0 0;color:#666">
          These contributed nothing today. Treat the list above as incomplete, not as a quiet day.</p>
      </div>""")

    parts.append(f"""
      <p style="color:#999;font-size:11px;margin-top:26px;border-top:1px solid #eee;padding-top:10px">
        Collected automatically from the SA opportunity sites. Closing dates are copied
        verbatim from the listings and are never inferred — where a source states none,
        this digest says so. Always confirm on the listing before applying.
      </p>
    </div>""")
    return "\n".join(parts)


def render_plan(plan):
    """Today's posting plan (social_plan.py) - what to post where, with which file."""
    rows = []
    for s in plan["slots"]:
        files = "<br>".join(f"<code style='font-size:11px'>{esc(f)}</code>" for f in s["files"])
        note = f"<div style='color:#666;font-size:12px;margin:4px 0'>{esc(s['note'])}</div>" if s["note"] else ""
        rows.append(f"""
      <div style="border:1px solid #eee;border-radius:8px;padding:10px 12px;margin:8px 0">
        <strong>{esc(s['time'])} · {esc(', '.join(s['platforms']))}</strong>
        <div style="margin:4px 0">{files}</div>{note}
        <pre style="background:#f6f6f6;padding:10px;border-radius:6px;font-size:12px;margin:6px 0 0;
                    white-space:pre-wrap;font-family:ui-monospace,Menlo,monospace">{esc(s['caption'])}</pre>
      </div>""")
    return (f"""
      <h2 style="font-size:15px;background:#f0f7ff;border-left:3px solid #1e6fd9;padding:8px 10px;margin:18px 0 6px">
        📲 Today's social plan — {esc(', '.join(plan['search_terms']))}</h2>
      <p style="font-size:12px;color:#666;margin:0">Times are SAST. The fliers for these slots are the
        first attachments. Every link is tracked (utm_campaign=opp_drop).</p>""" + "".join(rows))


def render_text(feed, when):
    items = feed["opportunities"]
    lines = [f"Campus Plug — opportunities for {when}", f"{len(items)} open opportunities", ""]
    for i in items:
        close = pretty(i.get("closing_date")) or "closing date not stated"
        n = i.get("days_left")
        left = f" ({n} day{'' if n == 1 else 's'} left)" if n is not None else ""
        lines += [f"- {i['title']}", f"  {close}{left} · {i.get('employer') or ''}", f"  {i['url']}"]
    for m in feed.get("sources_not_reporting", []) + feed.get("sources_needing_manual_fetch", []):
        lines.append(f"! not reporting: {m['name']} — {m['reason']}")
    return "\n".join(lines)


def flier_batches(when, live_ids, log, first=()):
    """The day's fliers as (captions_text, [[path, ...], ...]) - split to fit a mail.

    fliers.py lists them most-urgent first. Listings that closed since the
    fliers were rendered are left out, like everywhere else in this mail.
    """
    folder = ROOT / "out" / f"fliers-{when}"
    try:
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "", [], ""
    captions, batches, size = [], [[]], 0
    # The social plan's listings go first, so they land in the first mail.
    order = {i: n for n, i in enumerate(first)}
    entries = sorted(manifest.get("fliers", []), key=lambda e: order.get(e["id"], len(order)))
    for entry in entries:
        if entry["id"] not in live_ids:
            continue
        paths = [folder / name for name in entry["files"].values() if (folder / name).exists()]
        if not paths:
            continue
        captions += [f"=== {entry['title']}", "files: " + ", ".join(p.name for p in paths),
                     "", entry["caption"], ""]
        # A listing's formats stay together in one mail.
        weight = sum(p.stat().st_size for p in paths)
        if batches[-1] and size + weight > FLIER_BATCH_BYTES:
            batches.append([])
            size = 0
        batches[-1] += paths
        size += weight
    batches = [b for b in batches if b]
    log(f"fliers: {sum(len(b) for b in batches)} image(s) in {len(batches)} mail(s)")
    return "\n".join(captions), batches, manifest.get("play_url", "")


def send(subject, html_body, text_body, to_addr, log, fliers=(), captions=""):
    msg = EmailMessage()
    msg["From"] = f"{FROM_NAME} <{FROM_ADDRESS}>"
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    if captions:
        msg.add_attachment(captions.encode("utf-8"), maintype="text", subtype="plain",
                           filename="flier-captions.txt")
    for path in fliers:
        msg.add_attachment(path.read_bytes(), maintype="image", subtype="png",
                           filename=path.name)
    try:
        route = deliver_mail(msg, log)
    except Exception as exc:  # noqa: BLE001 - cron needs the reason in the log, not a traceback
        log(f"smtp: FAILED {exc}")
        return False
    log(f"smtp: delivered to {to_addr} via {route}")
    return True


def refresh_deadlines(feed, log):
    """Recompute days-left against today and drop anything now closed.

    The feed stores days_left as it was at collection time. A mail sent from a
    feed collected even one day earlier would otherwise advertise a deadline
    that has moved - and the countdown is the whole reason anyone opens this.
    """
    today = date.today()
    live, expired = [], 0
    for item in feed["opportunities"]:
        if item.get("closing_date"):
            left = (date.fromisoformat(item["closing_date"]) - today).days
            item["days_left"] = left
            if left < 0:
                expired += 1
                continue
        live.append(item)
    if expired:
        log(f"dropped {expired} listing(s) that closed since collection")
    feed["opportunities"] = live
    feed["by_category"] = {}
    for i in live:
        c = i.get("primary_category") or "other"
        feed["by_category"][c] = feed["by_category"].get(c, 0) + 1
    feed["by_category"] = dict(sorted(feed["by_category"].items(), key=lambda kv: -kv[1]))
    return live


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--feed", default=str(ROOT / "out" / "latest.json"))
    ap.add_argument("--to", default=DEFAULT_TO)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-empty", action="store_true",
                    help="send even when nothing new was collected")
    args = ap.parse_args()

    def log(m):
        print(m, flush=True)

    feed = json.loads(Path(args.feed).read_text(encoding="utf-8"))
    when = feed.get("collected_for", date.today().isoformat())
    items = refresh_deadlines(feed, log)

    pack_path = ROOT / "out" / f"posts-{when}.json"
    pack = json.loads(pack_path.read_text(encoding="utf-8")) if pack_path.exists() else None

    failures = feed.get("sources_not_reporting", [])
    if not items and not args.allow_empty:
        # Nothing new is normal on a quiet day and does not deserve a mail -
        # unless a source broke, which does.
        if failures:
            log("no new opportunities, but sources failed - sending the failure notice")
        else:
            log("no new opportunities and every source reported - no mail sent")
            return 0

    urgent = sum(1 for i in items
                 if i.get("days_left") is not None and 0 <= i["days_left"] <= URGENT_DAYS)
    subject = f"Campus Plug: {len(items)} new opportunities"
    if urgent:
        subject += f" — {urgent} closing within {URGENT_DAYS} days"
    if failures:
        subject += f" (⚠ {len(failures)} source{'s' if len(failures) > 1 else ''} down)"

    html_body = render_html(feed, pack, when)
    text_body = render_text(feed, when)

    plan_path = ROOT / "out" / f"social-plan-{when}.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.exists() else None
    if plan:
        html_body = html_body.replace("</p>", "</p>" + render_plan(plan), 1)
        text_body = (ROOT / "out" / f"social-plan-{when}.md").read_text(encoding="utf-8") + "\n\n" + text_body

    captions, batches, play_url = flier_batches(when, {i["id"] for i in items}, log,
                                                first=plan["heroes"] if plan else ())
    if batches:
        count = sum(len(b) for b in batches)
        note = (f"{count} ready-to-post fliers are attached"
                + (f" across {len(batches)} mails" if len(batches) > 1 else "")
                + ", most urgent first. flier-captions.txt has the caption for each.")
        html_body = html_body.replace(
            "</h1>", f'</h1>\n      <p style="font-size:13px;margin:0 0 10px">🖼️ {esc(note)}<br>'
                     f'App link for captions and bios: <a href="{esc(play_url)}">{esc(play_url)}</a></p>', 1)
        text_body = f"{note}\nApp link: {play_url}\n\n{text_body}"

    if args.dry_run:
        preview = ROOT / "logs" / "preview.html"
        preview.parent.mkdir(parents=True, exist_ok=True)
        preview.write_text(html_body, encoding="utf-8")
        log(f"dry run — subject: {subject}")
        log(f"dry run — wrote {preview}, nothing sent")
        return 0

    ok = send(subject, html_body, text_body, args.to, log,
              fliers=batches[0] if batches else (), captions=captions)
    # Fliers that did not fit in the first mail follow in their own.
    for n, batch in enumerate(batches[1:], start=2):
        part = f"Campus Plug fliers for {when} — mail {n} of {len(batches)}"
        ok = send(part, f"<p>{esc(part)}. Captions are in the first mail.</p>",
                  part, args.to, log, fliers=batch) and ok
    log(f"sent at {datetime.now():%F %T}" if ok else "send failed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
