"""Agents B (LLM half) + D: verify each candidate against its sources and write the feed content.
Free tier only: Groq gpt-oss (browser search) first, Gemini (Google Search grounding) as fallback.
Article text is fetched here in Python and given to the model as evidence; one call per candidate."""
import json
import time
from pathlib import Path

from . import fetch, llm

ROOT = Path(__file__).resolve().parent.parent
PROMPT = ROOT / "prompts" / "verify_and_create.md"

KEEP = ("cluster_key", "headline", "categories", "risk", "risk_terms", "tragedy", "verification", "outlets",
        "trend_hits", "age_hours", "sources")


def _search_note(cfg, entry, cand):
    """Models without a live search tool get Google News search headlines instead."""
    if entry.get("search"):
        return ""
    return ("\n\nYou have no web search tool. SEARCH_RESULTS (Google News ZA headlines for this story) =\n"
            + json.dumps(fetch.news_search(cfg, lambda m: None, cand["headline"][:120]), ensure_ascii=False))


def _one(cfg, slot, today, cand, log):
    """Returns a story dict (with `_writer`), or None if every free model failed."""
    ev = fetch.evidence(cfg, log, cand["sources"])
    prompt = (PROMPT.read_text() + f"\n\nToday is {today} (SAST). Slot: {slot}.\n\nCANDIDATE =\n"
              + json.dumps({k: cand[k] for k in KEEP}, ensure_ascii=False)
              + "\n\nEVIDENCE (article text fetched from the source URLs) =\n" + json.dumps(ev, ensure_ascii=False))
    conf = cfg["llm"]
    for provider in conf["writer_order"]:
        if not llm.available(provider) or not llm.chain(cfg, provider, "write"):
            continue
        try:
            text, meta = llm.call_chain(cfg, provider, "write", lambda m: prompt + _search_note(cfg, m, cand), log)
            llm.log_usage(meta, slot, today, "write")
            story = llm.parse_json(text)
        except llm.LLMError as exc:
            log(f"creator: {provider} failed on {cand['cluster_key']}: {str(exc)[:150]}")
            continue
        story["cluster_key"] = cand["cluster_key"]
        story["_writer"] = provider
        v = story.setdefault("verification", {})
        if isinstance(v, dict):  # record what was actually read, not what the model says it read
            v["sources_read"] = sorted(set((v.get("sources_read") or []) + [e["url"] for e in ev if e["fetched"]]))
        log(f"creator: {meta['model']} {meta['seconds']}s, {meta['searches']} searches - {story.get('decision')}: "
            f"{cand['headline'][:60]}")
        time.sleep(conf["pace_seconds"].get(provider, 10))
        return story
    return None


def run(cfg, slot, today, candidates, log, out_dir=ROOT / "out"):
    """Returns the list of stories, or None when no free provider is configured or all calls failed."""
    out_dir.mkdir(exist_ok=True)
    if not any(llm.available(p) for p in cfg["llm"]["writer_order"]):
        log("creator: no GROQ_API_KEY / GEMINI_API_KEY in ~/.config/campusplug/llm.env - nothing written")
        return None
    (out_dir / f"candidates-{today}-{slot}.json").write_text(json.dumps(
        {"slot": slot, "today": today, "candidates": [{k: c[k] for k in KEEP} for c in candidates]},
        indent=1, ensure_ascii=False))
    log(f"creator: free models on {len(candidates)} candidates ({slot})")
    stories = [s for s in (_one(cfg, slot, today, c, log) for c in candidates) if s]
    (out_dir / f"generated-{today}-{slot}.json").write_text(json.dumps({"stories": stories}, indent=1,
                                                                      ensure_ascii=False))
    return stories or None
