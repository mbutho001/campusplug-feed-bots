"""Independent second check (replaces human review): a different free model from the one that wrote the post
re-reads the evidence, searches the web, and returns pass/fail per post. Fails closed: no verdict = not published."""
import json
import time
from pathlib import Path

from . import editor, fetch, llm

ROOT = Path(__file__).resolve().parent.parent
PROMPT = ROOT / "prompts" / "audit.md"


def _provider_for(cfg, writer):
    """First configured auditor that did not write the post; same-model audits are not independent."""
    for p in cfg["llm"]["auditor_order"]:
        if p != writer and llm.available(p) and llm.chain(cfg, p, "audit"):
            return p
    return None


def run(cfg, slot, today, recs, log, writers=None, out_dir=ROOT / "out"):
    """Returns {post_id: (passed, reason)}; posts with no verdict are absent (= fail)."""
    writers = writers or {}
    verdicts, dump = {}, []
    conf = cfg["llm"]
    for r in recs:
        provider = _provider_for(cfg, writers.get(r["post_id"]))
        if provider is None:
            log(f"auditor: no independent free model for {r['post_id']} - failing closed")
            continue
        post = {"post_id": r["post_id"], "feed_text": editor.feed_text(cfg, r), "category": r["category"],
                "risk": r["risk"], "format": r["format"],
                "evidence": fetch.evidence(cfg, log, [s for s in r["sources"] if s.get("url")], max_sources=2),
                "snippets": [f"{s.get('outlet')}: {s.get('title', '')} - {s.get('summary', '')}"[:400]
                             for s in r["sources"] if s.get("title")][:5]}
        search_results = None

        def make_prompt(m):
            nonlocal search_results
            p = dict(post)
            if not m.get("search"):
                if search_results is None:
                    search_results = fetch.news_search(cfg, log, r["headline"][:120])
                p["search_results"] = search_results
            return PROMPT.read_text() + f"\n\nTODAY = {today} (SAST)\n\nPOST =\n" + json.dumps(p, ensure_ascii=False)
        try:
            text, meta = llm.call_chain(cfg, provider, "audit", make_prompt, log)
            llm.log_usage(meta, slot, today, "audit")
            v = llm.parse_json(text)
            verdicts[r["post_id"]] = (v.get("pass") is True, f"[{meta['model']}] " + str(v.get("reason", ""))[:190])
            log(f"auditor: {meta['model']} {'PASS' if v.get('pass') is True else 'FAIL'} {r['headline'][:60]}")
        except llm.LLMError as exc:
            log(f"auditor: {provider} gave no usable verdict for {r['post_id']} ({exc}) - failing closed")
        dump.append({"post": post, "verdict": verdicts.get(r["post_id"])})
        time.sleep(conf["pace_seconds"].get(provider, 10))
    (out_dir / f"audit-{today}-{slot}.json").write_text(json.dumps(dump, indent=1, ensure_ascii=False))
    return verdicts
