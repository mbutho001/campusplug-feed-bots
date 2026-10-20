"""Read a listing's own page for the facts the feed does not carry.

The flier design has slots for a stipend/value, who it is open to, and two
headline requirements. None of those are in the collected feed, so they are
read off the listing page by a free Groq model - and then every string it returns
is checked against the page text. Anything that is not literally on the page is
thrown away, and the flier falls back to what the feed does know.

    python3 enrich.py <listing-id>      # enrich one listing from out/latest.json

Results are cached in state/enrich.json, so a listing is fetched once.
"""
import json
import re
import sys
import threading
from pathlib import Path

import fetchlib
import requests

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "state" / "enrich.json"
LLM_ENV = Path.home() / ".config" / "campusplug" / "llm.env"
MODEL = "openai/gpt-oss-120b"  # free Groq tier
PAGE_CHARS = 14000
_CACHE_LOCK = threading.Lock()   # fliers.py enriches several listings at once

# Character limits from the design's FLIER_TEMPLATE.md.
MAX_VALUE, MAX_ELIGIBILITY, MAX_REQ = 18, 22, 55

PROMPT = """You are extracting facts from a South African student-opportunity listing.
Copy text EXACTLY as it appears in the page text below - same words, same order.
Do not paraphrase, summarise, translate or fix spelling. If the page does not
state something, use null. Reply with one JSON object and nothing else:

{
  "value_label": "Stipend" | "Amount" | "Covers" | null,
  "value": exact phrase of at most %d characters giving the stipend, salary or
           bursary amount/cover (e.g. "R5 000 per month"), or null,
  "eligibility": exact phrase of at most %d characters naming who may apply
           (e.g. "unemployed graduates"), or null,
  "requirements": list of the conditions an applicant must meet (qualification
           level, citizenship, age, employment status, documents), each an exact
           phrase of at most %d characters copied from the page. Do not list
           individual study fields, departments or positions as requirements.
}

Listing title: %s

PAGE TEXT:
%s
"""


def _norm(text):
    text = text.replace("’", "'").replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", text).strip().lower()


def _on_page(phrase, page_norm, limit):
    """True only for a non-trivial phrase that is literally in the page text."""
    if not isinstance(phrase, str):
        return False
    phrase = phrase.strip()
    if not 3 <= len(phrase) <= limit:
        return False
    # Whole words only: "R8 174,7" must not pass because "R8 174,75" is there.
    return re.search(r"(?<![\w,.])" + re.escape(_norm(phrase)) + r"(?![\w]|[,.]\d)", page_norm) is not None


def _groq_key():
    try:
        for line in LLM_ENV.read_text().splitlines():
            if line.startswith("GROQ_API_KEY="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return None


def _ask_claude(prompt, log):
    """Ask the free Groq model (name kept so callers are unchanged). Every answer is
    still checked against the page text by the caller, so a model slip is discarded."""
    key = _groq_key()
    if not key:
        log("    ! enrich: no GROQ_API_KEY")
        return None
    try:
        r = requests.post("https://api.groq.com/openai/v1/chat/completions",
                          headers={"Authorization": f"Bearer {key}"},
                          json={"model": MODEL, "temperature": 0, "max_tokens": 1500,
                                "reasoning_effort": "low",
                                "messages": [{"role": "user", "content": prompt}]},
                          timeout=120)
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"] or ""
    except Exception as exc:  # noqa: BLE001 - enrichment is optional, never fatal
        log(f"    ! groq: {exc}")
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None


def _load_cache():
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def enrich(item, log=print, use_cache=True):
    """Return {value_label, value, eligibility, requirements} - verified or empty."""
    empty = {"value_label": None, "value": None, "eligibility": None, "requirements": []}
    cache = _load_cache()
    if use_cache and item["id"] in cache:
        return cache[item["id"]]

    page = fetchlib.get(item["url"], log=log, retries=1)
    if not page:
        return empty                      # not cached: the site may answer tomorrow
    text = fetchlib.strip_tags(page)
    # Skip the site chrome above the listing where the title can be found.
    start = text.find(item["title"][:30])
    text = text[max(start, 0):][:PAGE_CHARS]
    page_norm = _norm(text)

    raw = _ask_claude(PROMPT % (MAX_VALUE, MAX_ELIGIBILITY, MAX_REQ, item["title"], text), log)
    if not isinstance(raw, dict):
        return empty

    out = dict(empty)
    # A value must actually name an amount or a cover, not just be on the page.
    value = raw.get("value")
    if _on_page(value, page_norm, MAX_VALUE) and re.search(r"R\s?\d|\d|tuition|full", value, re.I):
        out["value"] = value.strip()
        label = raw.get("value_label")
        out["value_label"] = label if label in ("Stipend", "Amount", "Covers") else "Value"
    if _on_page(raw.get("eligibility"), page_norm, MAX_ELIGIBILITY):
        out["eligibility"] = raw["eligibility"].strip()
    seen = set()
    for req in raw.get("requirements") or []:
        if _on_page(req, page_norm, MAX_REQ) and _norm(req) not in seen:
            seen.add(_norm(req))
            out["requirements"].append(req.strip())

    with _CACHE_LOCK:
        cache = _load_cache()
        cache[item["id"]] = out
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")
    return out


if __name__ == "__main__":
    feed = json.loads((ROOT / "out" / "latest.json").read_text(encoding="utf-8"))
    wanted = sys.argv[1] if len(sys.argv) > 1 else feed["opportunities"][0]["id"]
    for it in feed["opportunities"]:
        if it["id"].startswith(wanted):
            print(json.dumps(enrich(it, use_cache=False), indent=2, ensure_ascii=False))
