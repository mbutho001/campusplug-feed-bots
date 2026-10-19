"""Agent C - Trend analyst, plus the rule-based half of Agent B (corroboration + risk).

Groups recent items into story clusters, counts independent outlets, matches live trend topics,
classifies category and risk, and scores each cluster on separately stored components.
Nothing here invents a metric: a component with no data behind it is reported as unavailable.
"""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

WORD_RE = re.compile(r"[a-z0-9][a-z0-9'&-]+")
STOP = set("""a an the and or but if of to in on at by for with from as is are was were be been being it its
this that these those he she they we you i his her their our your them him who whom what which when where why
how not no yes do does did done has have had will would can could should may might must shall about after
before over under into out up down off again more most some such than too very just new says said say amid
also after year years day days week weeks month how why what video watch live update updates latest news
south africa african africans sa first last back get gets got make makes made one two three four five six
vs v here there now today tonight report reports over says told set sets
january february march april may june july august september october november december
monday tuesday wednesday thursday friday saturday sunday 2025 2026 2027""".split())

CATEGORIES = {
    "breaking_news":       ["breaking", "just in", "developing"],
    "politics":            ["anc", "democratic alliance", "eff", "mk party", "actionsa", "ifp", "parliament", "minister", "ramaphosa",
                            "zuma", "malema", "steenhuisen", "gnu", "cabinet", "election", "mayor", "council", "president"],
    "public_safety":       ["crime", "police", "saps", "murder*", "killed", "shooting*", "robbery", "hijack", "fire",
                            "flood", "storm", "accident", "crash", "protest*", "missing"],
    "local_news":          ["johannesburg", "joburg", "cape town", "durban", "pretoria", "tshwane", "ekurhuleni",
                            "soweto", "gqeberha", "bloemfontein", "polokwane", "mbombela", "kzn", "gauteng",
                            "western cape", "eastern cape", "limpopo", "mpumalanga", "free state", "north west"],
    "cost_of_living":      ["petrol", "fuel price", "diesel", "electricity", "tariff", "eskom", "load shedding",
                            "loadshedding", "food price", "inflation", "interest rate", "repo rate", "rand", "sassa",
                            "grant", "tax", "sars", "budget", "salary", "unemployment", "taxi fare", "water"],
    "education":           ["matric", "exam*", "school*", "university", "varsity", "tvet", "college*", "student*",
                            "lecturer*", "dhet", "basic education", "nsc", "matric results", "exam results", "degree", "graduat*", "campus"],
    "student_funding":     ["nsfas", "bursary", "bursaries", "scholarship*", "student funding", "allowance", "fees"],
    "jobs":                ["jobs", "internship*", "learnership*", "graduate programme", "vacancies", "hiring",
                            "employment", "youth employment", "job seekers"],
    "sport_football":      ["bafana", "psl", "betway premiership", "kaizer chiefs", "chiefs", "orlando pirates",
                            "pirates", "sundowns", "soccer", "football", "banyana", "afcon", "caf", "fifa", "premier league"],
    "sport_rugby_cricket": ["springboks", "boks", "rugby", "proteas", "cricket", "urc", "sharks", "stormers",
                            "bulls", "lions", "currie cup", "t20", "odi", "test match"],
    "music":               ["amapiano", "album*", "song*", "music", "artist*", "concert*", "festival*", "dj", "rapper*",
                            "hip hop", "gqom", "samas", "tour", "single"],
    "entertainment":       ["celeb*", "actor", "actress", "series*", "showmax", "netflix", "drama", "reality",
                            "big brother", "influencer*", "movie*", "film*", "tv", "soapie", "awards", "miss sa"],
    "viral":               ["viral", "tiktok*", "trend*", "meme*", "video", "x users", "twitter", "social media",
                            "mzansi reacts", "netizens", "online"],
    "youth_culture":       ["youth", "gen z", "slang", "fashion", "sneaker", "streetwear", "young people"],
    "technology":          ["ai", "artificial intelligence", "chatgpt", "tech", "app", "data", "mtn", "vodacom",
                            "telkom", "cell c", "smartphone", "iphone", "samsung", "starlink", "fibre", "cyber", "scam*"],
    "relationships":       ["dating", "relationship", "marriage", "wedding*", "lobola", "breakup", "love"],
    "student_life":        ["res", "residence", "digs", "student accommodation", "textbook", "assignment",
                            "test week", "exam stress", "freshers", "first-year"],
}

HIGH_RISK = ["killed", "murder*", "dead", "death", "died", "body", "bodies", "rape", "raped", "sexual", "shot",
             "shooting*", "stabbed*", "kidnap*", "abduct*", "accused", "arrested", "charged", "court", "trial",
             "allegedly", "alleged", "allegation*", "fraud", "corruption", "suicide", "abuse", "gbv", "femicide",
             "terror", "xenophob*", "racis*", "election", "vote", "voting", "outbreak", "cholera", "vaccine",
             "disease", "virus", "death toll", "victim*", "missing child", "assault", "hostage", "massacre",
             "funeral", "tragedy", "mourn", "passed away", "rip", "condolence"]
MEDIUM_RISK = ["anc", "eff", "mk party", "democratic alliance", "ramaphosa", "zuma", "malema", "protest*", "strike*", "minister",
               "police", "saps", "religion", "church", "immigra*", "foreign national", "lgbt", "gender"]
LOW_VALUE = ["lotto", "powerball", "horoscope", "gameday", "weather forecast", "how to watch", "live updates",
             "live blog", "preview", "betting tips", "odds", "promo code", "sponsored", "advertorial", "quiz:",
             "crossword", "deal of the day", "giveaway"]
TRAGEDY = ["killed", "murder*", "dead", "death", "died", "rape", "shooting*", "stabbed*", "suicide", "funeral",
           "tragedy", "mourn", "passed away", "victim*", "death toll", "massacre", "femicide", "gbv", "abuse"]

STUDENT_KW = ["nsfas", "student*", "student*", "campus", "varsity", "university", "tvet", "college*", "residence",
              "res", "exam*", "matric", "tuition", "fees", "graduat*", "bursary", "internship*", "learnership*",
              "youth", "young people", "gen z", "first job", "data", "airtime", "taxi", "load shedding", "rent",
              "wits", "uct", "tuks", "ukzn", "unisa", "cput", "dut", "nwu", "uj", "ufs", "rhodes", "stellenbosch",
              "nmu", "tut", "univen", "wsu", "ump", "spu", "smu"]
SA_KW = ["south africa", "mzansi", "johannesburg", "joburg", "cape town", "durban", "pretoria", "soweto", "eskom",
         "rand", "springboks", "bafana", "kaizer chiefs", "orlando pirates", "sundowns", "amapiano", "gauteng",
         "kzn", "kwazulu", "limpopo", "sassa", "nsfas", "saps", "ramaphosa", "anc", "proteas", "tshwane",
         "chiefs", "pirates", "banyana", "boks", "stormers", "sharks", "bulls", "psl", "betway premiership",
         "zuma", "malema", "mbalula", "parliament", "sars", "rand", "eastern cape", "western cape", "free state",
         "mpumalanga", "north west", "northern cape", "gqeberha", "bloemfontein", "polokwane", "tvet", "matric",
         "unisa", "wits", "uct", "ukzn", "tuks", "stellenbosch", "fort hare", "mzansi", "sabc", "mtn", "vodacom"]
SA_OUTLETS = {"news24", "sabc", "enca", "iol", "dailymaverick", "groundup", "sanews", "citizen", "thesouthafrican",
              "mybroadband", "kickoff", "briefly", "zalebs"}

ENGAGEMENT_BASE = {"student_life": 0.95, "student_funding": 0.9, "education": 0.85, "jobs": 0.85, "viral": 0.9,
                   "entertainment": 0.9, "music": 0.9, "sport_football": 0.9, "sport_rugby_cricket": 0.8,
                   "relationships": 0.9, "youth_culture": 0.85, "cost_of_living": 0.8, "technology": 0.7,
                   "local_news": 0.55, "politics": 0.5, "breaking_news": 0.6, "public_safety": 0.35}
TIER_CRED = {1: 1.0, 2: 0.75, 3: 0.5, 4: 0.25}


PREFIX_RE = re.compile(r"^(?:(?:news24|update|watch|breaking|live|just in|opinion)\s*[|:]\s*)+", re.I)


def clean_title(title):
    return PREFIX_RE.sub("", title).strip()


def overlap(a, b):
    """Shared share of the smaller title; needs two shared words so one name never merges stories."""
    shared = a & b
    return len(shared) / min(len(a), len(b)) if len(shared) >= 2 else 0.0


def same_topic(a, b):
    """Already-covered check: 40% of the smaller title, or 4+ shared words (team + opponent + competition)."""
    return overlap(a, b) >= 0.4 or len(a & b) >= 4


def tokens(text):
    return {w.strip("'-") for w in WORD_RE.findall(text.lower()) if w not in STOP and len(w) > 2}


def jaccard(a, b):
    return len(a & b) / len(a | b) if a and b else 0.0


def fingerprint(tok):
    return " ".join(sorted(tok)[:14])


_PATTERNS = {}


def _pattern(kw):
    """Whole-word match; a trailing * makes it a stem ("graduat*" -> graduate, graduation)."""
    if kw not in _PATTERNS:
        stem = kw.endswith("*")
        body = re.escape(kw.rstrip("*"))
        _PATTERNS[kw] = re.compile(r"(?<![a-z0-9])" + body + ("" if stem else r"(?![a-z0-9])"))
    return _PATTERNS[kw]


def _hits(text, words):
    return [w.rstrip("*") for w in words if _pattern(w).search(text)]


def _parse(ts):
    return datetime.fromisoformat(ts) if ts else None


def load_recent(con, max_age_h, now=None):
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=max_age_h)).isoformat()
    rows = con.execute("""select * from items
                          where coalesce(published_at, discovered_at) >= ? order by tier, published_at desc""",
                       (cutoff,)).fetchall()
    return [dict(r) for r in rows]


def recent_trends(con, hours=6, now=None):
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=hours)).isoformat()
    rows = con.execute("select platform, topic, min(rank) rank, max(volume) volume from trend_snapshots "
                       "where seen_at >= ? group by platform, lower(topic)", (cutoff,)).fetchall()
    return [dict(r) for r in rows]


def cluster(items, threshold):
    clusters = []
    for it in items:
        it["title"] = clean_title(it["title"])
        tok = tokens(it["title"] + " " + (it["summary"] or "")[:160])
        tok_title = tokens(it["title"])
        best, best_sim = None, 0.0
        for c in clusters:
            sim = max(overlap(tok_title, c["title_tokens"]), jaccard(tok, c["tokens"]))
            if sim > best_sim:
                best, best_sim = c, sim
        if best is not None and best_sim >= threshold:
            best["items"].append(it)
            best["tokens"] |= tok
        else:
            clusters.append({"items": [it], "tokens": set(tok), "title_tokens": tok_title})
    return clusters


def classify(text):
    """Categories ordered by keyword hits, so the first one is the story's main subject."""
    counts = {c: len(_hits(text, kws)) for c, kws in CATEGORIES.items()}
    cats = sorted((c for c in counts if counts[c]), key=lambda c: -counts[c])
    return cats or ["local_news"]


def risk_of(text):
    high = _hits(text, HIGH_RISK)
    if high:
        return "high", high
    med = _hits(text, MEDIUM_RISK)
    return ("medium", med) if med else ("low", [])


def score_clusters(cfg, items, trends, now=None, multipliers=None):
    now = now or datetime.now(timezone.utc)
    rules, w = cfg["rules"], cfg["weights"]
    multipliers = multipliers or {}
    official_ids = {f["id"] for f in cfg["sources"]["news_rss"] if f.get("official")}
    trends_available = bool(trends)
    trend_tok = [(t, tokens(t["topic"])) for t in trends]
    out = []
    items = [i for i in items if not _hits(" " + i["title"].lower() + " ", LOW_VALUE)]
    for c in cluster(items, rules["cluster_similarity"]):
        its = c["items"]
        news = [i for i in its if i["kind"] == "news"]
        outlets = sorted({re.sub(r"\W", "", i["source_name"].lower()) for i in news})
        lead = min(news or its, key=lambda i: (i["tier"], -(len(i["summary"] or ""))))
        text = " " + " ".join((i["title"] + " " + (i["summary"] or "")) for i in its).lower() + " "
        times = [_parse(i["published_at"]) or _parse(i["discovered_at"]) for i in its]
        newest = max(times)
        oldest = min(times)
        age_h = max(0.0, (now - newest).total_seconds() / 3600)
        head_text = " " + " ".join(i["title"] for i in its).lower() + " " + (lead["summary"] or "").lower() + " "
        cats = classify(head_text)
        risk, risk_words = risk_of(text)
        official = any(i["source_id"] in official_ids for i in its)

        # momentum: independent outlets, live trend topics, reddit chatter, and how fast outlets piled in
        trend_hits = []
        for t, tt in trend_tok:
            if sum(map(len, tt)) >= 4 and (tt <= c["tokens"] or len(tt & c["tokens"]) >= 2):
                trend_hits.append(f'{t["platform"]}: {t["topic"]}' + (f' ({t["volume"]})' if t["volume"] else ""))
        reddit_hits = sum(1 for i in its if i["kind"] == "reddit")
        span_h = max(1.0, (newest - oldest).total_seconds() / 3600)
        velocity = min(1.0, (len(outlets) - 1) / span_h) if len(outlets) > 1 else 0.0
        momentum = min(1.0, 0.22 * max(0, len(outlets) - 1) + 0.3 * len(trend_hits) + 0.12 * reddit_hits + 0.2 * velocity)

        comp = {
            "freshness": round(max(0.0, 1 - age_h / rules["max_story_age_hours"]), 3),
            "momentum": round(momentum, 3),
            "student_relevance": round(min(1.0, 0.34 * len(_hits(text, STUDENT_KW))), 3),
            "sa_relevance": round(min(1.0, (0.6 if any(i["source_id"] in SA_OUTLETS or i["kind"] == "reddit"
                                                       for i in its) else 0.1)
                                      + 0.2 * len(_hits(text, SA_KW))), 3),
            "engagement_potential": round(max(ENGAGEMENT_BASE.get(k, 0.5) for k in cats), 3),
            "credibility": 1.0 if official else TIER_CRED.get(min(i["tier"] for i in its), 0.25),
        }
        total = sum(w[k] * comp[k] for k in w)
        mult = max([multipliers.get(k, 1.0) for k in cats] or [1.0])
        total = round(min(1.0, total * mult), 3)

        if len(outlets) >= rules["min_independent_outlets"] or official:
            verification = "corroborated" if len(outlets) >= 2 else "official_source"
        elif news:
            verification = "single_source"
        else:
            verification = "social_only"

        rejects = []
        if age_h > rules["max_story_age_hours"]:
            rejects.append("stale")
        if not _hits(text, SA_KW + CATEGORIES["local_news"]) and not official:
            rejects.append("no clear South African angle")
        if verification == "social_only":
            rejects.append("no news source (social chatter only)")
        if verification == "single_source" and risk == "high":
            rejects.append("high-risk claim from a single source")

        key_src = lead["url"]
        out.append({
            "cluster_key": hashlib.sha1(key_src.encode()).hexdigest()[:16],
            "fingerprint": fingerprint(tokens(lead["title"])),
            "headline": lead["title"],
            "categories": cats,
            "risk": risk,
            "risk_terms": risk_words[:6],
            "tragedy": bool(_hits(text, TRAGEDY)),
            "verification": verification,
            "outlets": outlets,
            "official": official,
            "trend_hits": trend_hits[:5],
            "trend_data": "available" if trends_available else "unavailable",
            "reddit_mentions": reddit_hits,
            "age_hours": round(age_h, 1),
            "newest": newest.isoformat(timespec="seconds"),
            "components": comp,
            "multiplier": mult,
            "score": total,
            "rejects": rejects,
            "sources": [{"outlet": i["source_name"], "tier": i["tier"], "title": i["title"], "url": i["url"],
                         "published_at": i["published_at"], "summary": (i["summary"] or "")[:300]}
                        for i in sorted(its, key=lambda i: i["tier"])[:6]],
        })
    out.sort(key=lambda c: c["score"], reverse=True)
    return out


def recent_fingerprints(con, days):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    rows = con.execute("select fingerprint, json_extract(record, '$.headline') as h from posts where run_date >= ? "
                       "and slot not like 'community%' "
                       "and status in ('scheduled','published','needs_review','dropped','spare')", (cutoff,)).fetchall()
    return [set(r["fingerprint"].split()) | tokens(clean_title(r["h"] or "")) for r in rows]


def pick_for_slot(cfg, clusters, slot, used_fps, n):
    """Best eligible clusters for a slot: slot-preferred categories first, one per category, no repeats."""
    rules = cfg["rules"]
    prefer = cfg["slots"][slot]["prefer"]
    used_fps = list(used_fps)
    eligible = [c for c in clusters if not c["rejects"] and c["score"] >= rules["min_trend_score"]
                and not any(same_topic(set(c["fingerprint"].split()), fp) for fp in used_fps)]
    ranked = sorted(eligible, key=lambda c: (-(c["score"] + (0.12 if set(c["categories"]) & set(prefer) else 0))))
    picked, cats_used = [], set()
    for c in ranked:
        main = c["categories"][0]
        fp = set(c["fingerprint"].split())
        if (main in cats_used and len(picked) < n - 1) or any(same_topic(fp, u) for u in used_fps):
            continue
        picked.append(c)
        used_fps.append(fp)
        cats_used.add(main)
        if len(picked) >= n:
            break
    return picked


def dump(clusters, path: Path):
    path.write_text(json.dumps(clusters, indent=1, ensure_ascii=False, default=str))
