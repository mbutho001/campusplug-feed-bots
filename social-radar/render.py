"""Render the digest as a mobile-friendly HTML email plus a plain-text fallback."""
import html
from datetime import datetime

CSS_CARD = ("border:1px solid #e6e6ea;border-radius:12px;padding:14px;margin:0 0 14px;"
            "background:#ffffff")


def _chip(text, bg="#eef2ff", fg="#3730a3"):
    return (f'<span style="display:inline-block;background:{bg};color:{fg};font-size:11px;'
            f'font-weight:600;padding:3px 8px;border-radius:999px;margin-right:6px">'
            f'{html.escape(text)}</span>')


def build(picks, topics, templates, unavailable, stamp, cfg):
    sa_topics = [t for t in topics if t["platform"] != "Google Trends" and t["source"] == "South Africa"]
    when = stamp.strftime("%a %d %b %Y, %H:%M SAST")

    h = [f'<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
         f'background:#f6f7fb;padding:18px;color:#14141b">',
         f'<div style="max-width:640px;margin:0 auto">',
         f'<h1 style="font-size:20px;margin:0 0 2px">Campus Plug Social Radar</h1>',
         f'<p style="margin:0 0 18px;color:#6b6b78;font-size:13px">{html.escape(when)} &middot; '
         f'{len(picks)} post-ready images &middot; {len(topics)} trending topics &middot; images only, no clips</p>']

    if topics:
        h.append('<h2 style="font-size:15px;margin:18px 0 8px">Trending right now</h2>')
        h.append(f'<div style="{CSS_CARD}">')
        by_platform = {}
        for t in topics:
            by_platform.setdefault(f'{t["platform"]} — {t["source"]}', []).append(t)
        for label, rows in by_platform.items():
            links = " ".join(
                f'<a href="{html.escape(r["url"])}" style="color:#3730a3;text-decoration:none">'
                f'{html.escape(r["topic"])}</a>'
                + (f'<span style="color:#9a9aa8"> ({html.escape(r["volume"])})</span>' if r.get("volume") else "")
                + '<span style="color:#c9c9d4"> &middot; </span>'
                for r in rows)
            h.append(f'<p style="margin:0 0 10px;font-size:13px;line-height:1.9">'
                     f'<strong style="font-size:12px;color:#6b6b78">{html.escape(label)}</strong><br>{links}</p>')
        h.append('</div>')

    if picks:
        h.append('<h2 style="font-size:15px;margin:22px 0 8px">Ready to post</h2>')
        for p in picks:
            chips = _chip(p["platform"]) + _chip(p["tag"], "#ecfdf5", "#065f46")
            if p.get("matched"):
                chips += _chip("SA/student: " + ", ".join(p["matched"][:3]), "#fff7ed", "#9a3412")
            h.append(
                f'<div style="{CSS_CARD}">'
                f'<div style="margin-bottom:8px">{chips}</div>'
                f'<a href="{html.escape(p["permalink"])}" style="color:#14141b;text-decoration:none">'
                f'<div style="font-size:14px;font-weight:600;line-height:1.4;margin-bottom:10px">'
                f'{html.escape(p["title"])}</div>'
                f'<img src="cid:{p["cid"]}" width="{p["w"]}" style="max-width:100%;height:auto;'
                f'border-radius:8px;display:block"></a>'
                f'<p style="margin:10px 0 0;font-size:12px;color:#6b6b78">'
                f'{html.escape(p["source"])} &middot; {p["kb"]} KB &middot; '
                f'<a href="{html.escape(p["permalink"])}" style="color:#3730a3">open original</a> &middot; '
                f'<a href="{html.escape(p["image_url"])}" style="color:#3730a3">full-size image</a></p>'
                f'</div>')

    if sa_topics:
        ideas = ", ".join(html.escape(t["topic"]) for t in sa_topics[:5])
        h.append(f'<div style="{CSS_CARD}background:#f0f4ff">'
                 f'<strong style="font-size:13px">Angle for today</strong>'
                 f'<p style="margin:6px 0 0;font-size:13px;line-height:1.6;color:#3a3a48">'
                 f'Tie a Campus Plug post to what Mzansi is already talking about: {ideas}. '
                 f'Pair one of those with a meme above, or caption a blank template below.</p></div>')

    if templates:
        h.append('<h2 style="font-size:15px;margin:22px 0 8px">Blank meme templates</h2>')
        h.append(f'<div style="{CSS_CARD}"><p style="margin:0;font-size:13px;line-height:1.9">' +
                 " ".join(f'<a href="{html.escape(t["page"])}" style="color:#3730a3;text-decoration:none">'
                          f'{html.escape(t["name"])}</a><span style="color:#c9c9d4"> &middot; </span>'
                          for t in templates) + '</p></div>')

    if unavailable:
        rows = "".join(f'<li style="margin-bottom:4px">{html.escape(k)}: {html.escape(v)}</li>'
                       for k, v in unavailable.items())
        h.append(f'<div style="{CSS_CARD}background:#fffbeb">'
                 f'<strong style="font-size:13px">Platforms not reporting this run</strong>'
                 f'<ul style="margin:8px 0 0;padding-left:18px;font-size:12px;color:#6b6b78">{rows}</ul></div>')

    h.append('<p style="margin:18px 0 0;font-size:11px;color:#9a9aa8">Campus Plug Social Radar &middot; '
             'daily at 12:00 SAST &middot; images only, video excluded by design &middot; '
             'check rights before reposting anything you did not make.</p>')
    h.append('</div></div>')

    text = [f"Campus Plug Social Radar — {when}", ""]
    for t in topics[:20]:
        text.append(f"[{t['platform']}/{t['source']}] {t['topic']} {t.get('volume','')}".rstrip())
    text.append("")
    for p in picks:
        text.append(f"- {p['title']} ({p['source']}) {p['permalink']}")
    if unavailable:
        text.append("")
        text.append("Not reporting: " + "; ".join(f"{k}: {v}" for k, v in unavailable.items()))
    return "\n".join(h), "\n".join(text)


def subject(cfg, picks, topics, stamp):
    lead = topics[0]["topic"] if topics else "no live trends"
    return (f'{cfg["subject_prefix"]}: {len(picks)} memes + {len(topics)} trends '
            f'— {lead} ({stamp.strftime("%H:%M")})')
