"""Campaign copy: phases (aligned to the Plug Seasons milestones in the brand proposals), promo angles,
UGC-style Short scripts, and the claim guard.

Every promise comes from FACTS (what the live app does today) or from a LIVE prize in prizes.json.
Until a prize is funded and has rules, copy runs in TEASER mode: prizes are "coming", never "win".
An optional free-LLM pass (Groq, then Gemini) rewrites the long caption for variety; anything it
writes must pass guard() or the template caption is used instead.
"""
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "trend-bots"))

FOLLOW = ("IG, Threads + Facebook @campusplugsa · TikTok @campusplug_sa · X @CampusPlug_SA · "
          "YouTube @CampusPlugSa · WhatsApp Channel: Campus Plug SA")
TAGS = "#CampusPlug #Mzansi #StudentLife #SAStudents"
META_LINE = "This promotion is not sponsored, endorsed or run by Meta."

# Only what the live app does (points rules from migration 023; referral copy matches the app's invite sheet).
FACTS = [
    "Campus Plug is a free Android app for South African students (Google Play).",
    "Check in every day to collect Plug Points; 7, 14 and 21-day streaks give bonus points.",
    "Invite a friend with your code: you each get 10 Plug Points once they have checked in on 3 days (paid after 7 days, up to 3 friends a month).",
    "There is a poll in the app feed every day; results are shared every Monday.",
    "Bursaries, internships, learnerships and jobs are posted in the app every day, with closing dates.",
]

PHASES = [  # (first day, key, name, kicker)
    ("2026-11-13", "plugged", "Get plugged in", "Before Pens Down"),
    ("2026-11-26", "pensdown", "Pens Down", "Pens Down 2026"),
    ("2026-12-08", "festive", "Festive", "Festive season"),
    ("2027-01-01", "results", "Results & Ready", "Results season"),
    ("2027-01-19", "registration", "Registration", "Registration 2027"),
]

ANGLES_BY_PHASE = {
    "plugged": ["prizes", "follow", "streak", "poll", "invite", "bursaries"],
    "pensdown": ["pensdown", "prizes", "poll", "follow", "invite", "streak"],
    "festive": ["hustle", "prizes", "invite", "streak", "follow", "poll"],
    "results": ["results", "bursaries", "prizes", "poll", "follow", "streak"],
    "registration": ["registration", "invite", "prizes", "bursaries", "follow", "poll"],
}

STEPS_SETUP = [("Get Campus Plug", "Free on Android, link in bio"),
               ("Follow us everywhere", "@campusplugsa on IG, Threads, FB"),
               ("Check in daily", "Start your Plug Points streak")]


def phase(day):
    cur = PHASES[0]
    for p in PHASES:
        if day.isoformat() >= p[0]:
            cur = p
    return {"key": cur[1], "name": cur[2], "kicker": cur[3]}


def live_prizes(prizes, day):
    out = []
    for p in prizes:
        need = ("funded_by", "value_rand", "entry_opens", "entry_closes", "draw_date", "rules_url")
        if p.get("status") == "live" and all(p.get(k) for k in need) \
                and p["entry_opens"] <= day.isoformat() <= p["entry_closes"]:
            out.append(p)
    return out


def angle(day, key, prizes, cfg):
    """Poster + captions for one promo angle. Returns a dict (see render.promo_poster)."""
    ph = phase(day)
    live = live_prizes(prizes, day)
    rules = cfg["rules_url"]
    if live:
        p = live[0]
        prize_line = (f"This week: {p['name']} worth R{p['value_rand']:,} (by {p['funded_by']}). "
                      f"Enter free in the app: {p['how_to_enter']}. Closes {p['entry_closes']}, "
                      f"draw {p['draw_date']}. 18+ only. Rules: {p['rules_url']}").replace(",", " ")
        legal = f"Free entry, 18+, SA residents. Full rules: {p['rules_url']}. {META_LINE}"
    else:
        prize_line = ("We're lining up brand prizes for students: hampers, airtime and data, laptops and cash. "
                      "Each drop gets its own rules before entries open, so nobody gets caught out.")
        legal = "No prize is live yet. Every prize drop is announced with its rules before entry opens. Draws 18+."

    a = {
        "prizes": dict(
            chip="Weekly drops" if not live else "Live this week",
            headline="Prize drops are *coming*" if not live else f"Win {live[0]['name']} *this week*",
            sub=("Hampers, airtime, laptops, cash. Get set up now so you hear the second a drop opens."
                 if not live else prize_line),
            steps=STEPS_SETUP, screen="checkin", cta="Get set up: link in bio", show_prizes=True,
            caption=f"{prize_line}\n\nGet set up now:\n1. Download Campus Plug (free, Android)\n"
                    f"2. Follow us: {FOLLOW}\n3. Check in daily and vote in the daily poll\n\n"
                    f"You'll hear first when each drop opens.\n\n{TAGS} #PlugSeasons",
            short=("Brand prize drops for students are coming: hampers, airtime, laptops, cash. "
                   "Get the app + follow us so you hear first." if not live else prize_line[:240]),
            video="prizes"),
        "follow": dict(
            chip="Stay plugged", headline="One plug. *Every* platform.",
            sub="Bursaries, polls, campus news and the first word on prize drops. Follow wherever you scroll.",
            steps=[("Instagram, Threads, Facebook", "@campusplugsa"), ("TikTok", "@campusplug_sa"),
                   ("X and YouTube", "@CampusPlug_SA · @CampusPlugSa")],
            screen="tools", cta="Follow + get the app", show_prizes=False,
            caption=f"Follow Campus Plug where you already scroll:\n{FOLLOW}\n\n"
                    "Every day: bursaries and jobs closing soon, a student poll, and Mzansi campus talk. "
                    "When brand prize drops open, followers hear first.\n\n" + TAGS,
            short="Follow Campus Plug where you scroll: @campusplugsa (IG, Threads, FB), @campusplug_sa (TikTok). Bursaries, polls, prize-drop news.",
            video="follow"),
        "streak": dict(
            chip="Daily streak", headline="Tap in. *Every* day.",
            sub="One check-in a day collects Plug Points. Hit 7, 14 and 21 days for bonus points.",
            steps=[("Open Campus Plug", "Takes 3 seconds"), ("Tap Check in", "Points every day"),
                   ("Keep the streak alive", "Bonus at 7, 14, 21 days")],
            screen="checkin", cta="Start your streak", show_prizes=False,
            caption="Your daily 3-second habit: open Campus Plug, tap Check in, collect Plug Points. "
                    "Keep the streak going for bonus points at 7, 14 and 21 days.\n\n"
                    f"Free on Android, link in bio.\n\n{TAGS} #StreakSZN",
            short="3 seconds a day: open Campus Plug, tap Check in, collect Plug Points. Bonus at 7, 14 and 21 days.",
            video="streak"),
        "poll": dict(
            chip="Daily poll", headline="Your vote. *Every* day.",
            sub="A new student poll lands in the app feed every morning. Results drop every Monday.",
            steps=[("Open the feed", "New poll every morning"), ("Tap your answer", "One vote each"),
                   ("See the results", "Every Monday on our socials")],
            screen="poll", cta="Vote today", show_prizes=False,
            caption="Every morning there's a new poll in the Campus Plug feed: brands, campus life, money, "
                    "your future. Tap your answer, and every Monday we post what Mzansi students said.\n\n"
                    f"Free on Android, link in bio.\n\n{TAGS} #PlugPulse",
            short="New student poll in the Campus Plug feed every morning. Results every Monday. What will you pick?",
            video="poll"),
        "invite": dict(
            chip="Squad up", headline="Bring your *squad*",
            sub="Share your code. When your friend checks in on 3 days, you each get 10 Plug Points.",
            steps=[("Open Me, tap Invite", "Your code is waiting"), ("Send it to a friend", "WhatsApp it"),
                   ("They check in 3 days", "You both get 10 points")],
            screen="invite", cta="Share your code", show_prizes=False,
            caption="Campus Plug is better with your people. Share your invite code: once your friend has "
                    "checked in on 3 days, you each get 10 Plug Points (up to 3 friends a month).\n\n"
                    f"Free on Android, link in bio.\n\n{TAGS} #SquadInvite",
            short="Share your Campus Plug code. Friend checks in 3 days = you both get 10 Plug Points.",
            video="invite"),
        "bursaries": dict(
            chip="Free money alert", headline="Bursaries. Jobs. *Daily.*",
            sub="Every day we post bursaries, internships and learnerships that are still open, with the closing date.",
            steps=[("Open Bursaries & Jobs", "In Student tools"), ("Check the closing date", "Sorted soonest first"),
                   ("Apply before it closes", "Link in every post")],
            screen="tools", cta="Find yours today", show_prizes=False,
            caption="Stop missing closing dates. Campus Plug posts open bursaries, internships, learnerships "
                    "and jobs every day, each with the closing date and the official apply link.\n\n"
                    f"Free on Android, link in bio.\n\n{TAGS} #Bursaries #Internships",
            short="Bursaries, internships and jobs still open, posted daily with closing dates. Free on Campus Plug.",
            video="bursaries"),
        "pensdown": dict(
            chip=ph["kicker"], headline="Pens down. *Phone* up.",
            sub="Celebrate safe, get home safe. Then plan your summer with us: jobs, polls and prize-drop news.",
            steps=[("Celebrate the safe way", "Stick with your people"), ("Get Campus Plug", "Free, link in bio"),
                   ("Vote in the Pens Down poll", "How are you celebrating?")],
            screen="poll", cta="Get the app", show_prizes=True,
            caption="Last paper done? Respect. Celebrate the safe way: stick with your people, plan your ride "
                    "home, look after each other.\n\nThen get plugged in for summer: holiday jobs, bursaries "
                    f"for 2027 and the first word on brand prize drops.\n\n{prize_line}\n\n{TAGS} #PensDown",
            short="Pens down! Celebrate safe, get home safe. Then plug in for summer jobs, 2027 bursaries and prize-drop news.",
            video="pensdown"),
        "hustle": dict(
            chip="Festive hustle", headline="Broke season? *Not you.*",
            sub="Holiday jobs, learnerships and side-hustle tips in the app, plus a money poll every week.",
            steps=[("Check Bursaries & Jobs", "Holiday + 2027 posts"), ("Vote in the money poll", "Real tips from students"),
                   ("Keep your streak", "Points while you hustle")],
            screen="tools", cta="Plan your summer", show_prizes=False,
            caption="Festive season hits the wallet hard. Campus Plug posts holiday jobs, learnerships and "
                    "2027 opportunities daily, and students swap money tips in the feed.\n\n"
                    f"{prize_line}\n\nFree on Android, link in bio.\n\n{TAGS} #FestiveHustle",
            short="Broke season survival: holiday jobs + 2027 opportunities posted daily on Campus Plug.",
            video="hustle"),
        "results": dict(
            chip=ph["kicker"], headline="Results are *coming*",
            sub="Whatever your marks say, there's a next step. Late applications, bursaries and second chances, posted daily.",
            steps=[("Breathe", "Your marks are not your worth"), ("Check what's still open", "Bursaries & Jobs"),
                   ("Ask the community", "Students who've been there")],
            screen="tools", cta="Find your next step", show_prizes=False,
            caption="Matric results season. Whatever happens, you have options: late applications, bursaries, "
                    "learnerships and TVET routes are posted on Campus Plug every day.\n\n"
                    f"Free on Android, link in bio.\n\n{TAGS} #MatricResults #Class2026",
            short="Results season: late applications, bursaries and learnerships posted daily on Campus Plug. You have options.",
            video="results"),
        "registration": dict(
            chip=ph["kicker"], headline="Registration *ready?*",
            sub="Docs, funding, digs, transport. Students sort it together in the Campus Plug feed.",
            steps=[("Check your funding", "Bursaries still open"), ("Ask the feed", "Res, digs, transport"),
                   ("Bring your squad", "10 points each")],
            screen="poll", cta="Get registration-ready", show_prizes=False,
            caption="First-years and returning students: registration is close. Use Campus Plug to find "
                    "bursaries still open, ask about res and digs, and team up with students at your campus.\n\n"
                    f"{prize_line}\n\nFree on Android, link in bio.\n\n{TAGS} #Registration2027",
            short="Registration season: bursaries still open + students sorting res and digs together on Campus Plug.",
            video="registration"),
    }[key]
    a["legal"] = legal
    a["keep"] = KEEP.get(key, []) + (["rules"] if live else [])
    a["angle"] = key
    a["phase"] = ph["key"]
    a["live"] = bool(live)
    return a


# Phrases a rewrite must keep (case-insensitive), so the angle's message and its numbers survive.
KEEP = {
    "prizes": ["hampers", "laptops", "@campusplugsa"], "follow": ["@campusplugsa", "@campusplug_sa"],
    "streak": ["7, 14 and 21"], "poll": ["monday"], "invite": ["10 plug points", "3 days"],
    "bursaries": ["closing date"], "pensdown": ["safe"], "hustle": ["holiday jobs"],
    "results": ["late applications"], "registration": ["registration"],
}

# UGC-style Short scripts: (beats [(caption, screen, seconds)], voiceover). Brand voice, never a fake student.
SHORTS = {
    "prizes": ([("POV: you hear about a *prize drop* after it closed", None, 2.6),
                ("Don't be that person", None, 1.8),
                ("Step 1: get *Campus Plug*, free on Android", "checkin", 2.6),
                ("Step 2: follow *@campusplugsa*", "tools", 2.4),
                ("Step 3: check in *every day*", "checkin", 2.4),
                ("Hampers, airtime, laptops, cash: drops are *coming*", None, 3.0)],
               "Okay, quick one. Brand prize drops for students are coming to Campus Plug: hampers, airtime, "
               "laptops and cash. Every drop gets its own rules before it opens. So get set up now. Download "
               "Campus Plug, it's free. Follow us at campus plug S A. And check in every day, so you hear first."),
    "follow": ([("Where to find *Campus Plug*", None, 2.2),
                ("Instagram, Threads, Facebook: *@campusplugsa*", None, 2.8),
                ("TikTok: *@campusplug_sa*", None, 2.2),
                ("Bursaries + polls + campus talk, *daily*", "tools", 2.8),
                ("And first word on *prize drops*", None, 2.4)],
               "Here's where to find Campus Plug. Instagram, Threads and Facebook, at campus plug S A. TikTok, "
               "campus plug underscore S A. Every day we post bursaries closing soon, a student poll and campus "
               "talk. And you'll hear first when brand prize drops open."),
    "streak": ([("The *3-second* habit", None, 2.0),
                ("Open the app", "checkin", 2.0),
                ("Tap *Check in*", "checkin", 2.2),
                ("Bonus points at *7, 14, 21* days", "checkin", 2.8),
                ("Don't break the *streak*", None, 2.0)],
               "The three second habit. Open Campus Plug. Tap check in. Collect your Plug Points. Keep it going "
               "and you get bonus points at seven, fourteen and twenty one days. Don't break the streak."),
    "poll": ([("Students of Mzansi, *you decide*", None, 2.2),
              ("New poll every *morning*", "poll", 2.6),
              ("Brands. Money. Campus life.", "poll", 2.4),
              ("Results every *Monday*", None, 2.2),
              ("What are you voting *today*?", None, 2.0)],
             "Students of Mzansi, you decide. There's a new poll in the Campus Plug feed every morning. Brands, "
             "money, campus life. Every Monday we post the results. So, what are you voting today?"),
    "invite": ([("Your squad *needs* this", None, 2.0),
                ("Open Me, tap *Invite*", "invite", 2.6),
                ("Send your code on WhatsApp", "invite", 2.4),
                ("They check in *3 days*", "checkin", 2.2),
                ("You *both* get 10 points", None, 2.2)],
               "Your squad needs this. On Campus Plug, open Me and tap Invite. Send your code to a friend. Once "
               "they've checked in on three days, you both get ten Plug Points."),
    "bursaries": ([("Missed a bursary *again*?", None, 2.2),
                   ("Campus Plug posts them *daily*", "tools", 2.6),
                   ("With the *closing date*", "tools", 2.2),
                   ("And the official apply link", None, 2.2),
                   ("Free on Android, *link in bio*", None, 2.2)],
                  "Missed a bursary again? Campus Plug posts open bursaries, internships and learnerships every "
                  "day, with the closing date and the official apply link. It's free on Android. Link in bio."),
    "pensdown": ([("*Pens down* checklist", None, 2.0),
                  ("Celebrate with your *people*", None, 2.2),
                  ("Plan your ride *home*", None, 2.2),
                  ("Then plug in for *summer*", "tools", 2.6),
                  ("Jobs, bursaries, prize-drop news", "checkin", 2.6)],
                 "Pens down checklist. Celebrate with your people. Plan your ride home. Look after each other. "
                 "Then plug in for summer on Campus Plug: holiday jobs, bursaries for twenty twenty seven, and "
                 "first word on brand prize drops."),
    "hustle": ([("*Broke season* survival", None, 2.0),
                ("Holiday jobs, posted *daily*", "tools", 2.6),
                ("Money tips from *real students*", "poll", 2.6),
                ("Points while you *hustle*", "checkin", 2.4)],
               "Broke season survival. Campus Plug posts holiday jobs and learnerships every day, students share "
               "money tips in the feed, and you collect Plug Points just for checking in."),
    "results": ([("Results season, *breathe*", None, 2.2),
                 ("Your marks are *not* your worth", None, 2.4),
                 ("Late apps + bursaries, *daily*", "tools", 2.6),
                 ("You have *options*", None, 2.0)],
                "Results season. Breathe. Your marks are not your worth. Late applications, bursaries and "
                "learnerships are posted on Campus Plug every day. You have options."),
    "registration": ([("Registration *loading*", None, 2.0),
                      ("Funding? Check what's *open*", "tools", 2.6),
                      ("Res, digs, transport: *ask the feed*", "poll", 2.8),
                      ("Bring your squad: *10 points* each", "invite", 2.6)],
                     "Registration is loading. Check which bursaries are still open on Campus Plug. Ask the feed "
                     "about res, digs and transport. And bring your squad: you each get ten Plug Points."),
}

BANNED_TEASER = [r"\bwin(s|ning|ner|ners)?\b", r"\bwon\b", r"\bgiveaway\b", r"\bguarantee", r"\bR\s?\d",
                 r"\bredeem", r"campus cash", r"\b(25|50)\s*(pts|points)\b", r"\bfree laptop", r"\bup for grabs\b",
                 r"\bprize (money|pool) of\b"]
ALWAYS_BANNED = [r"campus cash", r"\b(25|50)\s*(pts|points)\b", r"\bguarantee", r"\bshare (this|to enter)\b",
                 r"\btag (\d|a|your) friends? to (enter|win)\b"]


def guard(text, live):
    """Problems with a piece of copy (empty list = OK)."""
    low = text.lower()
    bad = [p for p in (ALWAYS_BANNED if live else BANNED_TEASER + ALWAYS_BANNED) if re.search(p, low)]
    if live and re.search(r"\bwin\b", low) and "rules" not in low:
        bad.append("win without rules link")
    return bad


def freshen(a, log=print):
    """Optional free-LLM rewrite of the long caption. Falls back to the template on any problem."""
    try:
        from engine import llm
    except Exception:  # noqa: BLE001
        return a["caption"], "template"
    prompt = (
        "Rewrite this Instagram/Facebook caption for South African university and TVET students. Warm, "
        "short sentences, a little Mzansi slang is fine (no slurs, no alcohol). Keep the SAME message, the "
        "same order and every number exactly as written. Do NOT add any fact, number, feature, prize, brand or "
        "promise that is not already in the caption. "
        + ("Prizes are NOT live: never say win, won, winner, giveaway, up for grabs or any rand amount. "
           if not a["live"] else "Keep the rules link exactly. ")
        + "Keep the follow handles and the hashtag line exactly. Max 900 characters. "
        "Return JSON only: {\"caption\": \"...\"}\n\nCaption:\n" + a["caption"])
    for provider, model in (("groq", "openai/gpt-oss-120b"), ("gemini", "gemini-flash-latest")):
        if not llm.available(provider):
            continue
        try:
            text, _ = llm.call(provider, prompt, model, search=False, timeout=90)
            out = (llm.parse_json(text) or {}).get("caption", "").strip()
        except Exception as exc:  # noqa: BLE001
            log(f"freshen {provider}: {exc!r}")
            continue
        missing = [k for k in a.get("keep", []) if k.lower() not in out.lower()]
        if 80 < len(out) <= 1500 and not missing and not guard(out, a["live"]):
            return out, f"{provider}/{model}"
        log(f"freshen {provider}: rejected ({guard(out, a['live']) or missing or 'length'})")
    return a["caption"], "template"


def load_json(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


if __name__ == "__main__":
    cfg, prizes = load_json("config.json"), load_json("prizes.json")["prizes"]
    d = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
    for k in ANGLES_BY_PHASE[phase(d)["key"]]:
        a = angle(d, k, prizes, cfg)
        print(k, guard(a["caption"] + a["short"] + a["sub"] + a["headline"], a["live"]))
