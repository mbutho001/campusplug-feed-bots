"""Free-tier LLM clients only: Groq (gpt-oss with built-in browser search) and Google Gemini (with Google Search
grounding). No paid API and no Claude subscription is used for posting or research. Keys live in
~/.config/campusplug/llm.env (GROQ_API_KEY, GEMINI_API_KEY)."""
import json
import os
import re
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = Path.home() / ".config" / "campusplug" / "llm.env"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
THINK_RE = re.compile(r"<think>.*?</think>", re.S)  # qwen reasoning, never shown
CITE_RE = re.compile(r"\s?【[^】]{0,40}】")  # gpt-oss browser citations, never shown in posts
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class LLMError(Exception):
    pass


class RateLimited(LLMError):
    pass


_cooldown = {}  # "provider/model" -> unix time it may be tried again (rate-limited models are skipped)


def keys():
    out = {k: os.environ.get(k, "") for k in ("GROQ_API_KEY", "GEMINI_API_KEY")}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                if k.strip() in out and not out[k.strip()]:
                    out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def available(provider):
    return bool(keys()["GROQ_API_KEY" if provider == "groq" else "GEMINI_API_KEY"])


def _post(url, headers, body, timeout, retries=3):
    """POST with short backoff. A rate limit that will not clear within ~30 s raises at once, so the caller
    moves straight on to the next free model instead of waiting out a daily quota."""
    for attempt in range(retries):
        try:
            r = requests.post(url, headers=headers, json=body, timeout=timeout)
        except requests.RequestException as exc:
            if attempt == retries - 1:
                raise LLMError(f"network: {type(exc).__name__}") from exc
            time.sleep(5 * (attempt + 1))
            continue
        if r.status_code == 200:
            return r.json()
        wait = r.headers.get("retry-after", "")
        wait = float(wait) if wait.replace(".", "").isdigit() else None
        if r.status_code == 429:
            if wait is not None and wait <= 30 and attempt < retries - 1:
                time.sleep(wait + 1)
                continue
            raise RateLimited(f"HTTP 429: {r.text[:200]}")
        if r.status_code in (500, 502, 503, 504) and attempt < retries - 1:
            time.sleep(10 * (attempt + 1))
            continue
        raise LLMError(f"HTTP {r.status_code}: {r.text[:300]}")
    raise LLMError("retries exhausted")


def groq(prompt, model, search=True, timeout=180, system=None):
    """Returns (text, meta). gpt-oss models on Groq run the browser_search tool server-side."""
    key = keys()["GROQ_API_KEY"]
    if not key:
        raise LLMError("GROQ_API_KEY not set")
    body = {"model": model, "temperature": 0.2, "max_completion_tokens": 8192, "reasoning_effort": "low" if not search else "medium",
            "messages": ([{"role": "system", "content": system}] if system else []) +
                        [{"role": "user", "content": prompt}]}
    if search:
        body["tools"] = [{"type": "browser_search"}]
        body["tool_choice"] = "auto"
    t0 = time.time()
    res = _post(GROQ_URL, {"Authorization": f"Bearer {key}"}, body, timeout)
    msg = (res.get("choices") or [{}])[0].get("message") or {}
    u = res.get("usage") or {}
    meta = {"provider": "groq", "model": model, "seconds": round(time.time() - t0),
            "input": u.get("prompt_tokens"), "output": u.get("completion_tokens"),
            "searches": len(msg.get("executed_tools") or [])}
    return CITE_RE.sub("", msg.get("content") or ""), meta


def gemini(prompt, model, search=True, timeout=180, system=None):
    """Returns (text, meta). Google Search grounding is part of the Gemini free tier (daily-limited)."""
    key = keys()["GEMINI_API_KEY"]
    if not key:
        raise LLMError("GEMINI_API_KEY not set")
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 8192}}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if search:
        body["tools"] = [{"google_search": {}}]
    t0 = time.time()
    res = _post(GEMINI_URL.format(model=model), {"x-goog-api-key": key}, body, timeout)
    cand = (res.get("candidates") or [{}])[0]
    text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []) if not p.get("thought"))
    u = res.get("usageMetadata") or {}
    grounding = cand.get("groundingMetadata") or {}
    meta = {"provider": "gemini", "model": model, "seconds": round(time.time() - t0),
            "input": u.get("promptTokenCount"), "output": u.get("candidatesTokenCount"),
            "searches": len(grounding.get("webSearchQueries") or [])}
    if not text:
        raise LLMError(f"empty response (finish: {cand.get('finishReason')})")
    return text, meta


def call(provider, prompt, model, **kw):
    return (groq if provider == "groq" else gemini)(prompt, model, **kw)


def chain(cfg, provider, role):
    """Free models for a provider, best first, that may do this role ("write" or "audit")."""
    now = time.time()
    return [m for m in cfg["llm"]["models"].get(provider, [])
            if role in m.get("roles", ["write", "audit"]) and _cooldown.get(f"{provider}/{m['model']}", 0) <= now]


def call_chain(cfg, provider, role, make_prompt, log, search=None):
    """Tries each free model of the provider in turn. make_prompt(entry) builds the prompt for that model
    (models without live search get search results pasted in). Returns (text, meta) or raises LLMError."""
    conf, last = cfg["llm"], None
    for m in chain(cfg, provider, role):
        try:
            text, meta = call(provider, make_prompt(m), m["model"],
                              search=m.get("search", False) if search is None else (search and m.get("search", False)),
                              timeout=conf["timeout_seconds"])
            text = THINK_RE.sub("", text)
            if role == "write" or text.strip():
                return text, meta
        except RateLimited as exc:
            _cooldown[f"{provider}/{m['model']}"] = time.time() + conf.get("cooldown_seconds", 900)
            log(f"llm: {provider}/{m['model']} rate-limited - trying next free model")
            last = exc
        except LLMError as exc:
            log(f"llm: {provider}/{m['model']} failed ({str(exc)[:120]}) - trying next free model")
            last = exc
    raise last or RateLimited(f"no {provider} model available for {role} (all cooling down)")


def parse_json(text):
    """Pulls the first JSON object out of a model reply (search tools rule out strict JSON mode)."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start = text.find("{")
    if start < 0:
        raise LLMError("no JSON object in reply")
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError as exc:
        raise LLMError(f"invalid JSON in reply ({exc.msg})") from exc
    if not isinstance(obj, dict):
        raise LLMError("reply JSON is not an object")
    return obj


def log_usage(meta, slot, today, step):
    row = {"date": today, "slot": slot, "step": step, "cost_usd": 0, **meta}
    (ROOT / "state").mkdir(exist_ok=True)
    with open(ROOT / "state" / "llm_usage.jsonl", "a") as f:
        f.write(json.dumps(row) + "\n")
