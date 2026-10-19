"""Agent E - Editor: assemble the full post record, validate it against the schema and the editorial
rules, decide auto-publish vs human review, assign the bot and the time slot."""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import jsonschema

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / "schema" / "post.schema.json").read_text())
SAST = ZoneInfo("Africa/Johannesburg")

BANNED = ["nsfw", "onlyfans", "porn", "nude", "gore", "kill yourself", "kys", "k-word", "kaffir", "coolie",
          "makwerekwere", "faggot", "retard"]
TRAGEDY_FORMATS = {"summary", "explainer", "discussion"}
CREDIBLE_DOMAINS = ["sabcnews.com", "news24.com", "enca.com", "iol.co.za", "dailymaverick.co.za", "groundup.org.za",
                    "sanews.gov.za", "gov.za", "ac.za", "timeslive.co.za", "citizen.co.za", "thesouthafrican.com",
                    "bbc.co.uk", "bbc.com", "reuters.com", "apnews.com", "mybroadband.co.za", "kickoff.com",
                    "briefly.co.za", "zalebs.com", "businesstech.co.za", "moneyweb.co.za", "ewn.co.za",
                    "sowetanlive.co.za", "news.google.com", "goal.com", "supersport.com", "sarugby.co.za",
                    "kaizerchiefs.com", "orlandopiratesfc.com", "sundownsfc.co.za", "nsfas.org.za", "dhet.gov.za",
                    "702.co.za", "capetalk.co.za", "jacarandafm.com", "aljazeera.com", "theguardian.com"]
FORMAT_EMOJI = {"summary": "📰", "poll": "🗳️", "discussion": "💬", "humour": "😂", "explainer": "💡", "quiz": "🧠"}
CATEGORY_TAG = {"sport_football": "#PSL", "sport_rugby_cricket": "#Bokke", "music": "#Amapiano",
                "student_funding": "#NSFAS", "education": "#StudentLife", "student_life": "#StudentLife",
                "jobs": "#Careers", "technology": "#Tech", "entertainment": "#Mzansi", "viral": "#Trending",
                "cost_of_living": "#MoneyTalk", "politics": "#SANews", "breaking_news": "#SANews",
                "local_news": "#SANews", "public_safety": "#SANews", "relationships": "#CampusTalk",
                "youth_culture": "#Mzansi", "campus": "#CampusTalk"}


def _domain_ok(url):
    host = urlsplit(url).netloc.lower().split(":")[0]
    return any(host == d or host.endswith("." + d) for d in CREDIBLE_DOMAINS)


def _shingles(text, n=13):
    words = re.findall(r"[a-z0-9']+", text.lower())
    return {" ".join(words[i:i + n]) for i in range(max(0, len(words) - n + 1))}


def build_record(cfg, cluster, story, slot, run_date, discovered_at):
    v = story.get("verification") or {}
    # link to the outlet itself, never a news.google.com redirect, when any direct link exists
    blocked = set(cfg.get("hard_to_fetch", []))
    direct = sorted((x for x in cluster["sources"] if "news.google.com" not in x["url"]),
                    key=lambda x: any(b in x["url"] for b in blocked))  # stable: fetchable outlets first
    extra = [{"outlet": x.get("outlet") or "", "url": x["url"]} for x in (v.get("extra_sources") or [])
             if isinstance(x, dict) and str(x.get("url", "")).startswith("http") and "news.google.com" not in x["url"]
             and _domain_ok(x["url"])]
    lead = (direct or extra or cluster["sources"])[0]
    chosen = str(story.get("link_url") or "")
    for x in direct + extra:
        if x["url"] == chosen and _domain_ok(chosen):
            lead = x
            break
    fmt = story.get("primary_format", "summary")
    expires = datetime.now(timezone.utc) + timedelta(hours=int(story.get("expires_hours") or 24))
    return {
        "post_id": f"tb-{run_date.replace('-', '')}-{slot}-{cluster['cluster_key']}",
        "run_date": run_date,
        "slot": slot,
        "cluster_key": cluster["cluster_key"],
        "fingerprint": cluster["fingerprint"],
        "bot": None,
        "headline": (story.get("headline") or "").strip(),
        "caption": (story.get("caption") or "").strip(),
        "body": (story.get("body") or "").strip(),
        "category": story.get("category") if story.get("category") in cluster["categories"] else cluster["categories"][0],
        "tags": [str(t)[:40] for t in (story.get("tags") or [])][:8],
        "format": fmt,
        "source_url": lead["url"],
        "source_outlet": lead["outlet"],
        "source_published_at": lead.get("published_at"),
        "discovered_at": discovered_at,
        "verification_status": v.get("status", "unverified"),
        "verification_notes": str(v.get("notes", ""))[:1500],
        "confidence": float(v.get("confidence") or 0),
        "trend_score": cluster["score"],
        "score_components": cluster["components"],
        "trend_evidence": cluster["trend_hits"],
        "geo": story.get("geo") or "national",
        "audience": story.get("audience") or "SA students",
        "poll": story.get("poll") if fmt == "poll" else None,
        "image_instructions": story.get("image_instructions") or "",
        "attribution": story.get("attribution") or f"Reporting: {lead['outlet']}",
        "risk": story.get("risk") or cluster["risk"],
        "risk_notes": story.get("risk_notes") or "",
        "approval_status": "needs_review",
        "review_reasons": [],
        "scheduled_for": None,
        "publication_status": "draft",
        "metrics": {},
        "expires_at": expires.isoformat(timespec="seconds"),
        "formats": story.get("formats") or {},
        "sources": cluster["sources"] + [{"outlet": s.get("outlet"), "url": s.get("url")}
                                         for s in (v.get("extra_sources") or []) if isinstance(s, dict)],
        "analyst": {"verification": cluster["verification"], "outlets": cluster["outlets"],
                    "risk": cluster["risk"], "risk_terms": cluster["risk_terms"], "tragedy": cluster["tragedy"]},
    }


RISK_ORDER = {"low": 0, "medium": 1, "high": 2}


def review(cfg, rec):
    """Returns (hard_errors, review_reasons). Hard errors reject the post outright."""
    errors, reasons = [], []
    try:
        jsonschema.validate(rec, SCHEMA)
    except jsonschema.ValidationError as exc:
        errors.append(f"schema: {'/'.join(map(str, exc.absolute_path))} {exc.message[:160]}")

    # risk can only go up from the keyword rules
    if RISK_ORDER.get(rec["risk"], 2) < RISK_ORDER[rec["analyst"]["risk"]]:
        rec["risk"] = rec["analyst"]["risk"]
    text = f" {rec['headline']} {rec['body']} ".lower()
    if any(re.search(r"(?<![a-z])" + re.escape(b) + r"(?![a-z])", text) for b in BANNED):
        errors.append("banned word")
    if not _domain_ok(rec["source_url"]):
        errors.append(f"source domain not on the credible list: {urlsplit(rec['source_url']).netloc}")
    src_text = " ".join(f"{s.get('title', '')} {s.get('summary', '')}" for s in rec["sources"])
    if _shingles(rec["body"]) & _shingles(src_text):
        errors.append("copies 13+ consecutive words from a source")
    if rec["format"] == "poll":
        opts = (rec["poll"] or {}).get("options") or []
        if len({o.strip().lower() for o in opts}) != len(opts) or len(opts) < 2:
            errors.append("poll needs 2-4 distinct options")
    if rec["analyst"]["tragedy"] and rec["format"] not in TRAGEDY_FORMATS:
        errors.append(f"tragedy story cannot be a {rec['format']}")
    if rec["verification_status"] in ("disputed", "unverified"):
        errors.append(f"verification {rec['verification_status']}")
    if rec["expires_at"] and rec["expires_at"] < datetime.now(timezone.utc).isoformat():
        errors.append("expired")

    if rec["risk"] == "high":
        reasons.append("high risk: " + ", ".join(rec["analyst"]["risk_terms"][:4] or [rec["risk_notes"][:60]]))
    if rec["risk"] == "medium" and rec["category"] in ("politics", "public_safety"):
        reasons.append("medium-risk politics/safety")
    if rec["verification_status"] == "allegation":
        reasons.append("story is an allegation")
    if rec["verification_status"] == "single_source" or rec["analyst"]["verification"] == "single_source":
        reasons.append("single source")
    if rec["confidence"] < cfg["rules"]["min_confidence_auto"]:
        reasons.append(f"confidence {rec['confidence']:.2f}")
    if re.search(r"[\"“][^\"”]{30,}[\"”]", rec["body"]):
        reasons.append("contains a direct quote - check it against the source")
    return errors, reasons


SAFE_SINGLE_SOURCE = {"sport_football", "sport_rugby_cricket", "music", "entertainment", "technology",
                      "education", "youth_culture", "student_life", "viral"}
MONEY_FACTS = {"cost_of_living", "student_funding", "jobs"}
NEUTRAL_FORMATS = {"summary", "explainer", "discussion"}


def auto_decide(cfg, rec):
    """No human in the loop: every post the review rules would hold is either cleared by a stricter automatic
    rule or dropped. Returns (publish, reason). The independent auditor still checks everything that passes."""
    pol = cfg["auto_policy"]
    a = rec["analyst"]
    outlets = len(a["outlets"]) + (1 if a["verification"] == "official_source" else 0)
    conf = rec["confidence"]
    if a["tragedy"] or rec["verification_status"] == "allegation":
        return False, "tragedy or allegation - never auto-published"
    if rec["risk"] == "high":
        return False, "high risk - never auto-published"
    if re.search(r"[\"“][^\"”]{30,}[\"”]", rec["body"]) and outlets < 2:
        return False, "direct quote from a single source"
    if conf < pol["min_confidence"]:
        return False, f"confidence {conf:.2f} < {pol['min_confidence']}"
    if rec["risk"] == "medium":
        if outlets < 2 or conf < pol["min_confidence_medium"] or rec["format"] not in NEUTRAL_FORMATS:
            return False, "medium risk needs 2+ outlets, high confidence and a neutral format"
    single = outlets < 2 or rec["verification_status"] == "single_source"
    if single:
        tiers = [x.get("tier", 9) for x in rec["sources"] if x.get("url") == rec["source_url"]] or [9]
        if rec["category"] in MONEY_FACTS:
            return False, "money / grant / deadline facts need 2+ outlets or an official source"
        if rec["category"] not in SAFE_SINGLE_SOURCE or rec["risk"] != "low":
            return False, f"single source not allowed for {rec['category']} / {rec['risk']} risk"
        if min(tiers) > 2 or conf < pol["min_confidence_single"]:
            return False, "single source must be a major outlet with high confidence"
    return True, "cleared by automatic policy"


def bot_for(cfg, category):
    for email, b in cfg["bots"].items():
        if category in b["categories"]:
            return email
    return "naledi.bot@campusplug.com"


def slot_times(cfg, slot, n, now=None):
    now = now or datetime.now(timezone.utc)
    s = cfg["slots"][slot]
    base = datetime.now(SAST).replace(hour=s["hour"], minute=s["minute"], second=0, microsecond=0)
    base = max(base.astimezone(timezone.utc), now + timedelta(minutes=5))
    return [(base + timedelta(minutes=i * cfg["space_minutes"])).isoformat(timespec="seconds") for i in range(n)]


def feed_text(cfg, rec):
    lines = [f"{FORMAT_EMOJI.get(rec['format'], '📰')} {rec['headline']}", "", rec["body"]]
    if rec["format"] == "poll" and rec["poll"] and not cfg.get("native_polls"):
        letters = "ABCD"
        lines += ["", f"🗳️ {rec['poll']['question']}"]
        lines += [f"{letters[i]}) {o}" for i, o in enumerate(rec["poll"]["options"])]
        lines += ["Drop your letter in the comments 👇"]
    lines += ["", f"📰 {rec['attribution']}", "🔗 Read more:", rec["source_url"], "",
              f"#CampusPlug {CATEGORY_TAG.get(rec['category'], '#Mzansi')}"]
    return "\n".join(lines)
