#!/usr/bin/env python3
"""Campus Plug — the copy for the WhatsApp Channel, TikTok and YouTube Shorts posts.

Each platform is read differently, so each gets its own shape (the rules are in the
campusplug-social-autopost skill):

    WhatsApp Channel  a scannable roundup people forward: bold title, one line per
                      opportunity, a clickable tracked link, "forward this" ask.
    TikTok            hook first (it shows above the fold), the names to search,
                      "link in bio" (links in captions are not clickable), a comment
                      question, the brand hashtag set.
    YouTube Shorts    a searchable title under 100 characters with #Shorts, a
                      description with the clickable tracked link, tags.

Same rule as everywhere: the apply link never appears — the reader searches the name
in the app. Every number is counted from the listings, never invented.
"""
from posts import APP_NAME, CATEGORY_EMOJI, plural, pretty_date, search_term, urgency

from fliers import tracked_play_url

# The brand hashtag set (the social accounts brief), then platform reach tags.
BRAND_TAGS = ["#CampusPlug", "#StudentLifeSA", "#MzansiStudents", "#UniLifeSA", "#TVETstudents"]
CATEGORY_HASHTAG = {
    "bursary": "#Bursaries2027", "internship": "#InternshipsSA", "learnership": "#LearnershipsSA",
    "apprenticeship": "#Apprenticeships", "in-service training": "#InServiceTraining",
    "graduate programme": "#GraduateProgramme",
}
TIKTOK_LIMIT = 2200
YT_TITLE_LIMIT = 100
YT_DESC_LIMIT = 5000
WHATSAPP_LIMIT = 1000


def _cat(item):
    return item.get("primary_category") or "opportunity"


def _line(item, with_date=True):
    emoji = CATEGORY_EMOJI.get(_cat(item), "📢")
    close = pretty_date(item.get("closing_date")) if with_date else None
    return f"{emoji} {search_term(item)} — {_cat(item)}" + (f" (closes {close})" if close else "")


def _mix(items):
    """'bursaries & internships' style phrase from the categories in the set."""
    cats = []
    for i in items:
        c = _cat(i)
        if c not in cats:
            cats.append(c)
    words = [plural(c, 2) for c in cats[:2]]
    return " & ".join(words) if words else "opportunities"


def _soonest(items):
    left = [i["days_left"] for i in items if i.get("days_left") is not None and i["days_left"] >= 0]
    return min(left) if left else None


def headline(items):
    """The hook used on the video intro card and as the first caption line."""
    n, soon = len(items), _soonest(items)
    noun = "opportunity" if n == 1 else "opportunities"
    if soon is not None and soon <= 7:
        when = "today" if soon == 0 else ("tomorrow" if soon == 1 else f"in {soon} days")
        return f"{n} {noun} closing soon — the first closes {when}"
    return f"{n} {noun} open right now"


def _tags(items, extra=()):
    tags = list(BRAND_TAGS)
    for i in items:
        t = CATEGORY_HASHTAG.get(_cat(i))
        if t and t not in tags:
            tags.append(t)
    for t in extra:
        if t not in tags:
            tags.append(t)
    return tags


# ------------------------------------------------------------------ WhatsApp Channel

def whatsapp(items, all_items, when):
    """The channel roundup. WhatsApp renders *bold* and _italic_; links are clickable."""
    soon = [i for i in all_items if i.get("days_left") is not None and 0 <= i["days_left"] <= 7]
    lines = [f"*📢 {APP_NAME} opportunity drop — {pretty_date(when) or when}*", ""]
    lines += [_line(i) for i in items]
    if len(all_items) > len(items):
        lines += ["", f"+ {len(all_items) - len(items)} more in the app today"
                  + (f", {len(soon)} closing within 7 days ⏳" if soon else "") + "."]
    lines += ["", "_Search the name in the app for the requirements and the apply link._", "",
              f"📲 Get {APP_NAME} (free, Android): {tracked_play_url('whatsapp', 'channel')}", "",
              "🔁 Forward this to someone who needs it. React ❤️ if you're applying."]
    text = "\n".join(lines)
    if len(text) > WHATSAPP_LIMIT and len(items) > 1:
        return whatsapp(items[:-1], all_items, when)
    return text[:WHATSAPP_LIMIT]


# ------------------------------------------------------------------ TikTok

def tiktok(items):
    lines = [headline(items) + " 👀", ""]
    lines += [_line(i) for i in items]
    urge = next((urgency(i) for i in items if urgency(i)), None)
    if urge:
        lines += ["", f"⏳ {urge}"]
    lines += ["", f"Search the name on {APP_NAME} for the apply link — free app, link in bio 📲",
              "Save this so you don't miss the deadline 📌", "",
              "Which one are you applying for? Comment 👇", "",
              " ".join(_tags(items, ["#SouthAfrica", "#fyp"]))]
    return "\n".join(lines)[:TIKTOK_LIMIT]


# ------------------------------------------------------------------ YouTube Shorts

def youtube(items):
    """(title, description, tags) for the Short."""
    n = len(items)
    mix = _mix(items).title()
    title = f"{n} {mix} Closing Soon in South Africa 🇿🇦 #Shorts"
    if len(title) > YT_TITLE_LIMIT:
        title = f"{n} Opportunities Closing Soon in SA #Shorts"
    desc = [headline(items) + ".", ""]
    desc += [_line(i) for i in items]
    desc += ["", f"Search the name on {APP_NAME} for the full requirements and the apply link.",
             f"📲 Get {APP_NAME} free on Android: {tracked_play_url('youtube', 'shorts')}", "",
             f"New bursaries, internships and learnerships go up on {APP_NAME} every day — "
             "subscribe so you never miss a closing date.", "",
             " ".join(_tags(items, ["#Shorts"]))]
    tags = ["Campus Plug", "bursaries South Africa", "internships South Africa", "learnerships",
            "student life SA", "TVET", "matric", "graduate programmes"] + [search_term(i) for i in items]
    return title[:YT_TITLE_LIMIT], "\n".join(desc)[:YT_DESC_LIMIT], tags[:15]


# ------------------------------------------------------------------ video voiceover

def voiceover(items):
    """What the en-ZA voice says over the Short (roughly 3 seconds per line)."""
    parts = [headline(items).replace("—", ",") + "."]
    for i in items:
        close = pretty_date(i.get("closing_date"))
        close = close.rsplit(" ", 1)[0] if close else None   # "13 November" - the year slows the voice down
        parts.append(f"{search_term(i)}, {_cat(i)}" + (f", closes {close}." if close else "."))
    parts.append(f"Search the name on {APP_NAME} for the apply link. It's free.")
    return " ".join(parts)
