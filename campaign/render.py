"""Campus Plug campaign creative: posters (portrait / square / story) and UGC-style vertical Shorts.

Brand = the app's spaza wall (DESIGN.md): whitewash #F4EFE4, ink #1A1A1A, one orange action #F37023,
yellow #F2C230 and blue #2F6FB5 paint, Lilita One headings, Figtree body. Rendered with headless
Chrome through the opportunity fliers' screenshot() so fonts and Chrome flags stay in one place.

The Shorts are "UGC style" in format only: phone-first 9:16, big caption text that changes every
couple of seconds, a South African voice, real app screens. They never show an invented person or
a made-up testimonial or win: the voice speaks for Campus Plug, not as a student.
"""
import html
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OPP = ROOT.parent / "opportunities" / "scripts"
sys.path.insert(0, str(OPP))

from fliers import ASSETS, screenshot  # noqa: E402

SCREENS = ROOT / "assets" / "screens"
QR = {"portrait": ASSETS / "qr-play-portrait.png", "square": ASSETS / "qr-play-square.png",
      "story": ASSETS / "qr-play-story.png"}

# Small inline icons (no emoji: the CI runner has no colour emoji font).
ICONS = {
    "hamper": '<svg viewBox="0 0 64 64"><path d="M8 26h48l-5 30H13z" fill="#F2C230" stroke="#1A1A1A" stroke-width="4" stroke-linejoin="round"/><path d="M20 26l8-16M44 26l-8-16" stroke="#1A1A1A" stroke-width="4" stroke-linecap="round"/><path d="M22 36v12M32 36v12M42 36v12" stroke="#1A1A1A" stroke-width="4" stroke-linecap="round"/></svg>',
    "airtime": '<svg viewBox="0 0 64 64"><rect x="18" y="6" width="28" height="52" rx="6" fill="#2F6FB5" stroke="#1A1A1A" stroke-width="4"/><path d="M26 22a10 10 0 0 1 12 0M23 17a15 15 0 0 1 18 0" stroke="#fff" stroke-width="4" fill="none" stroke-linecap="round"/><circle cx="32" cy="27" r="3" fill="#fff"/><path d="M27 49h10" stroke="#fff" stroke-width="4" stroke-linecap="round"/></svg>',
    "laptop": '<svg viewBox="0 0 64 64"><rect x="12" y="12" width="40" height="28" rx="4" fill="#fff" stroke="#1A1A1A" stroke-width="4"/><path d="M4 50h56l-6-10H10z" fill="#F37023" stroke="#1A1A1A" stroke-width="4" stroke-linejoin="round"/></svg>',
    "cash": '<svg viewBox="0 0 64 64"><rect x="6" y="16" width="52" height="32" rx="5" fill="#7BC47F" stroke="#1A1A1A" stroke-width="4"/><circle cx="32" cy="32" r="8" fill="#fff" stroke="#1A1A1A" stroke-width="4"/><path d="M14 24v16M50 24v16" stroke="#1A1A1A" stroke-width="4" stroke-linecap="round"/></svg>',
}
PRIZE_ICON = {"brand-hamper": "hamper", "airtime": "airtime", "laptop": "laptop", "cash": "cash"}

STYLE = """
html,body{margin:0;width:%(w)dpx;height:%(h)dpx;overflow:hidden}
*{box-sizing:border-box}
body{background:#F4EFE4;color:#1A1A1A;font-family:'Figtree',sans-serif;position:relative;
  padding:%(pad)dpx %(pad)dpx 0;display:flex;flex-direction:column}
body:before{content:"";position:absolute;inset:0;pointer-events:none;opacity:.5;
  background-image:radial-gradient(rgba(26,26,26,.05) 1.2px,transparent 1.3px);background-size:22px 22px}
.top{display:flex;align-items:center;gap:18px;position:relative}
.top img{width:%(logo)dpx;height:%(logo)dpx}
.top b{font-family:'Lilita One';font-weight:400;font-size:%(brand)dpx;letter-spacing:.5px}
.top .chip{margin-left:auto;background:#F2C230;border:3px solid #1A1A1A;border-radius:12px;
  font-weight:800;font-size:%(chip)dpx;letter-spacing:3px;text-transform:uppercase;padding:10px 18px}
h1{font-family:'Lilita One';font-weight:400;font-size:%(h1)dpx;line-height:.98;margin:%(gap)dpx 0 0;position:relative}
h1 em{font-style:normal;color:#F37023;-webkit-text-stroke:0}
.sub{font-size:%(sub)dpx;font-weight:600;line-height:1.28;color:#4A463F;margin:%(gap2)dpx 0 0;position:relative;max-width:92%%}
.row{display:flex;gap:%(rowgap)dpx;margin-top:%(gap)dpx;position:relative;align-items:stretch}
.prize{flex:1;background:#fff;border:4px solid #1A1A1A;border-radius:20px;padding:18px 10px 16px;text-align:center;
  box-shadow:6px 6px 0 #1A1A1A}
.prize svg{width:%(icon)dpx;height:%(icon)dpx;display:block;margin:0 auto 8px}
.prize span{display:block;font-weight:800;font-size:%(plabel)dpx;line-height:1.1}
.prize i{display:inline-block;margin-top:10px;font-style:normal;font-weight:800;font-size:%(ptag)dpx;letter-spacing:1.5px;
  text-transform:uppercase;background:#2F6FB5;color:#fff;border-radius:8px;padding:5px 9px}
.prize.live i{background:#F37023;color:#1A1A1A}
.main{flex:1;display:flex;gap:%(rowgap)dpx;margin-top:%(gap)dpx;position:relative;min-height:0;overflow:hidden;padding:6px 16px 16px 6px}
.phone{flex:0 0 %(phw)dpx;border:6px solid #1A1A1A;border-radius:44px;overflow:hidden;background:#fff;
  box-shadow:10px 10px 0 #1A1A1A;transform:rotate(-3deg);align-self:flex-start;height:%(phh)dpx}
.phone img{width:100%%;display:block}
.steps{flex:1;display:flex;flex-direction:column;gap:%(stepgap)dpx;justify-content:center}
.step{display:flex;gap:16px;align-items:flex-start;font-size:%(step)dpx;font-weight:700;line-height:1.2}
.step b{flex:0 0 %(num)dpx;height:%(num)dpx;border-radius:50%%;background:#1A1A1A;color:#F4EFE4;display:flex;
  align-items:center;justify-content:center;font-family:'Lilita One';font-weight:400;font-size:%(numf)dpx}
.step small{display:block;font-weight:500;color:#5E5A52;font-size:%(stepsub)dpx;margin-top:4px}
.opts{display:flex;flex-direction:column;gap:%(stepgap)dpx;margin-top:%(gap)dpx;position:relative;flex:1;justify-content:center;padding-bottom:%(gap)dpx}
.opt{background:#fff;border:4px solid #1A1A1A;border-radius:18px;padding:%(optpad)dpx 26px;font-size:%(opt)dpx;font-weight:800;
  box-shadow:6px 6px 0 #1A1A1A;display:flex;gap:18px;align-items:center}
.opt b{font-family:'Lilita One';font-weight:400;color:#F37023;font-size:%(optl)dpx}
.foot{margin-top:auto;display:flex;align-items:flex-end;gap:22px;padding:%(footpad)dpx 0 %(pad)dpx;position:relative}
.cta{background:#F37023;border:4px solid #1A1A1A;border-radius:16px;font-weight:900;font-size:%(cta)dpx;padding:20px 30px;
  box-shadow:6px 6px 0 #1A1A1A}
.handles{font-size:%(handle)dpx;font-weight:700;line-height:1.35;color:#1A1A1A}
.handles span{color:#5E5A52;font-weight:600}
.qr{margin-left:auto;width:%(qr)dpx;height:%(qr)dpx;border:4px solid #1A1A1A;border-radius:14px;background:#fff;padding:6px}
.legal{position:absolute;left:%(pad)dpx;right:%(pad)dpx;bottom:14px;font-size:%(legal)dpx;color:#6E6A63;font-weight:600}
.story{justify-content:flex-start}
.label{display:inline-block;margin-top:%(gap)dpx;background:#2F6FB5;color:#fff;font-weight:800;letter-spacing:3px;
  font-size:%(chip)dpx;padding:10px 18px;border-radius:12px;position:relative;align-self:flex-start}
.quote{margin-top:%(gap2)dpx;background:#fff;border:4px solid #1A1A1A;border-radius:22px;padding:30px 34px;
  box-shadow:8px 8px 0 #1A1A1A;font-size:%(sub)dpx;font-weight:600;line-height:1.34;position:relative}
.quote small{display:block;margin-top:18px;font-size:%(legal2)dpx;color:#6E6A63;font-weight:700}
"""

SIZES = {
    "portrait": dict(w=1080, h=1350, pad=64, logo=64, brand=40, chip=24, h1=104, gap=34, gap2=18, sub=36,
                     rowgap=18, icon=78, plabel=26, ptag=17, phw=250, phh=420, stepgap=20, step=32, stepsub=24,
                     num=56, numf=32, opt=44, optl=54, optpad=30, footpad=24, cta=32, handle=24, qr=150,
                     legal=17, legal2=24),
    "square": dict(w=1080, h=1080, pad=56, logo=56, brand=36, chip=22, h1=92, gap=26, gap2=14, sub=32,
                   rowgap=16, icon=64, plabel=23, ptag=15, phw=260, phh=440, stepgap=14, step=28, stepsub=21,
                   num=48, numf=28, opt=32, optl=40, optpad=16, footpad=18, cta=28, handle=21, qr=124,
                   legal=15, legal2=21),
    "story": dict(w=1080, h=1920, pad=72, logo=72, brand=44, chip=28, h1=128, gap=48, gap2=24, sub=44,
                  rowgap=20, icon=96, plabel=30, ptag=19, phw=460, phh=980, stepgap=28, step=38, stepsub=28,
                  num=66, numf=36, opt=54, optl=66, optpad=40, footpad=30, cta=40, handle=30, qr=190,
                  legal=20, legal2=28),
}

HANDLES = ('IG · Threads · FB <span>@campusplugsa</span><br>TikTok <span>@campusplug_sa</span> · X <span>@CampusPlug_SA</span>')


def esc(s):
    return html.escape(s or "")


def headline(text):
    """'Prize drops are *coming*' -> the starred word in orange."""
    parts = esc(text).split("*")
    return "".join(f"<em>{p}</em>" if i % 2 else p for i, p in enumerate(parts))


def page(fmt, body, legal=""):
    s = SIZES[fmt]
    logo = (ASSETS / "campusplug-logo.png").as_uri()
    return (f'<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="{ASSETS.as_uri()}/fonts.css">'
            f'<style>{STYLE % s}</style></head><body class="{fmt}">'
            f'<div class="top"><img src="{logo}"><b>Campus Plug</b>{body.pop("chip", "")}</div>'
            f'{body["main"]}'
            f'<div class="foot">{body.get("foot", "")}<img class="qr" src="{QR[fmt].as_uri()}"></div>'
            f'<div class="legal">{esc(legal)}</div></body></html>')


def chip(text):
    return f'<span class="chip">{esc(text)}</span>'


def prize_row(prizes):
    cards = []
    for p in prizes:
        live = p["status"] == "live"
        tag = (f'R{p["value_rand"]:,}'.replace(",", " ") if live and p.get("value_rand") else "Live") if live else "Soon"
        cards.append(f'<div class="prize{" live" if live else ""}">{ICONS[PRIZE_ICON.get(p["id"], "hamper")]}'
                     f'<span>{esc(p["name"].replace("a laptop", "Laptop").capitalize())}</span><i>{esc(tag)}</i></div>')
    return f'<div class="row">{"".join(cards)}</div>'


def steps_block(steps):
    return '<div class="steps">' + "".join(
        f'<div class="step"><b>{n}</b><div>{esc(t)}{f"<small>{esc(sub)}</small>" if sub else ""}</div></div>'
        for n, (t, sub) in enumerate(steps, 1)) + "</div>"


def promo_poster(fmt, c, prizes, dest, workdir):
    """c: {chip, headline, sub, steps:[(t, sub)], screen, cta, legal, show_prizes}"""
    s = SIZES[fmt]
    screen = (SCREENS / f"{c.get('screen', 'checkin')}.jpg").as_uri()
    main = f'<h1>{headline(c["headline"])}</h1><div class="sub">{esc(c["sub"])}</div>'
    if c.get("show_prizes"):
        main += prize_row(prizes)
    if fmt == "square":
        main += f'<div class="main">{steps_block(c["steps"][:3])}</div>'
    else:
        main += (f'<div class="main"><div class="phone"><img src="{screen}"></div>'
                 f'{steps_block(c["steps"][:3])}</div>')
    foot = f'<div><div class="cta">{esc(c["cta"])}</div><div class="handles" style="margin-top:14px">{HANDLES}</div></div>'
    screenshot(page(fmt, {"chip": chip(c["chip"]), "main": main, "foot": foot}, c.get("legal", "")), _fmt(fmt), dest, workdir)
    return dest


def poll_poster(fmt, question, options, kicker, dest, workdir, legal=""):
    letters = "ABCD"
    opts = "".join(f'<div class="opt"><b>{letters[i]}</b>{esc(o)}</div>' for i, o in enumerate(options))
    main = (f'<h1>{headline(question)}</h1><div class="opts">{opts}</div>')
    foot = ('<div><div class="cta">Vote in the app</div><div class="handles" style="margin-top:14px">'
            'Or comment your letter below. Results every Monday.</div></div>')
    screenshot(page(fmt, {"chip": chip(kicker), "main": main, "foot": foot}, legal), _fmt(fmt), dest, workdir)
    return dest


def pulse_card(fmt, label, title, summary, source, dest, workdir):
    main = (f'<span class="label">{esc(label)}</span><h1 style="font-size:{int(SIZES[fmt]["h1"] * .72)}px">{esc(title)}</h1>'
            f'<div class="quote" style="font-size:{int(SIZES[fmt]["sub"] * 1.18)}px">{esc(summary)}<small>Source: {esc(source)}</small></div>')
    foot = ('<div><div class="cta">Join the talk on Campus Plug</div><div class="handles" style="margin-top:14px">'
            'Free for SA students. Link in bio.</div></div>')
    screenshot(page(fmt, {"chip": chip("Mzansi today"), "main": main, "foot": foot}), _fmt(fmt), dest, workdir)
    return dest


def _fmt(fmt):
    return fmt  # fliers.FORMATS uses the same names


# ---------------------------------------------------------------- UGC-style Short

CAPTION_CARD = """<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="{assets}/fonts.css">
<style>html,body{{margin:0;width:1080px;height:1920px;overflow:hidden}}
body{{background:#F4EFE4;font-family:'Figtree',sans-serif;position:relative}}
.phone{{position:absolute;left:200px;top:{top}px;width:680px;height:1120px;border:8px solid #1A1A1A;border-radius:60px;overflow:hidden;
  box-shadow:14px 14px 0 #1A1A1A;transform:rotate({rot}deg);background:#fff}}
.phone img{{width:100%;display:block}}
.cap{{position:absolute;left:70px;right:70px;top:{captop}px;text-align:center}}
.cap span{{display:inline;background:#1A1A1A;color:#F4EFE4;font-family:'Lilita One';font-size:{size}px;line-height:1.32;
  padding:6px 22px;box-decoration-break:clone;-webkit-box-decoration-break:clone;border-radius:14px}}
.cap span em{{font-style:normal;color:#F2C230}}
.tag{{position:absolute;left:70px;bottom:90px;font-weight:800;font-size:34px;color:#1A1A1A;
  background:#F2C230;border:4px solid #1A1A1A;border-radius:12px;padding:12px 20px}}
.logo{{position:absolute;right:70px;bottom:80px;width:110px}}
</style></head><body>{phone}<div class="cap"><span>{text}</span></div>
<div class="tag">@campusplugsa</div><img class="logo" src="{logo}"></body></html>"""


def caption_frame(text, screen, dest, workdir, n):
    phone = (f'<div class="phone"><img src="{(SCREENS / (screen + ".jpg")).as_uri()}"></div>' if screen else "")
    size = 92 if len(text) < 40 else 78 if len(text) < 70 else 64
    pg = CAPTION_CARD.format(assets=ASSETS.as_uri(), logo=(ASSETS / "campusplug-logo.png").as_uri(),
                             phone=phone, text=headline(text), size=size, top=500 if screen else 0,
                             captop=170 if screen else 720, rot=(-2, 2, -1, 1)[n % 4])
    screenshot(pg, "story", dest, workdir)
    return dest


def ugc_short(beats, voice_text, dest, voice=True, voice_name="en-ZA-LukeNeural"):
    """beats: [(caption, screen-or-None, seconds)]. Returns the video length in seconds."""
    import shorts
    shorts.ensure_ffmpeg()
    shorts.VOICE = voice_name
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        frames = [caption_frame(t, s, tmp / f"b{n}.png", tmp, n) for n, (t, s, _) in enumerate(beats)]
        durations = [d for _, _, d in beats]
        vo, vo_len = shorts.make_voice(voice_text, tmp / "vo.mp3") if voice else (None, 0.0)
        if vo_len:
            # Stretch the beats evenly so the voice ends before the last frame.
            need = vo_len + 1.2 + shorts.FADE * (len(beats) - 1)
            if need > sum(durations):
                k = need / sum(durations)
                durations = [d * k for d in durations]
        return shorts.render(frames, durations, vo, dest)


def results_poster(fmt, question, options, pct, total, dest, workdir):
    top = max(pct) if pct else 0
    bars = "".join(
        f'<div class="opt" style="position:relative;overflow:hidden"><span style="position:absolute;left:0;top:0;bottom:0;'
        f'width:{p}%;background:{"#F2C230" if p == top else "#E9E2D2"}"></span>'
        f'<b style="position:relative">{p}%</b><span style="position:relative">{esc(o)}</span></div>'
        for o, p in zip(options, pct))
    main = f'<h1>{headline(question)}</h1><div class="opts">{bars}</div>'
    foot = (f'<div><div class="cta">Vote in today\'s poll</div><div class="handles" style="margin-top:14px">'
            f'{total} student votes. Indicative, not a scientific sample.</div></div>')
    screenshot(page(fmt, {"chip": chip("Plug Pulse results"), "main": main, "foot": foot}), fmt, dest, workdir)
    return dest
