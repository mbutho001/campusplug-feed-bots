#!/usr/bin/env python3
"""Plug Store <- Core Group (Apple Reseller Program) daily sync, deals and season engine.

Runs on GitHub Actions through tick.py:
  store-sync   06:20 SAST daily   full check: new price list, products, prices, deals, hero, feed posts, mail
  store-check  13:20 SAST daily   light check: Core availability, cost guard, deal start/end (no list probing)

  python3 run.py sync [--dry-run] [--force-build]
  python3 run.py check [--dry-run]
  python3 run.py status

Pricing rules
  regular price  = ceil(Core RRP incl. VAT x 1.20)   (Core promo RRP while Core runs a promo)
  Plug Deal      = ceil(Core RRP incl. VAT x 1.15), shown with compare-at = the regular price
  A product only goes on a Plug Deal after it has sold at its regular price for 7+ days and not been
  on a deal in the last 21 days, so the "was" price is always a real price (CPA s29/s41).
  Rounds are intermittent: N deals (season slots) for D days, then a rest of G days (seasons.py).

Price list discovery (the Download link sits behind the reseller login):
  1. state/pricelist_url.txt (drop a link there, or ask Claude to fetch it via Chrome)
  2. the price-list page (works if Core ever shows the link logged out)
  3. Core's CDN, probing YYYYMMDD_ARP_Price_list_<Campaign>_<Month>_<Year>.xlsx names since the last list
If none is found but Core's public wholesale prices moved, it mails an alert and protects margins
(variants whose Core cost leaves < 5 % margin are hidden until the new list arrives). Core promo SKUs are
hidden after the promo end date until a new list arrives (their regular RRP isn't known).

Private data (price lists, costs, deal history) stays in state/ -> the private campusplug-feed-state repo.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import random
import re
import sys
import traceback
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import core  # noqa: E402
import hero  # noqa: E402
from seasons import season_for  # noqa: E402

SAST = ZoneInfo("Africa/Johannesburg")
STATE_DIR = ROOT / "state"
STATE = STATE_DIR / "state.json"
XLSX = STATE_DIR / "pricelist.xlsx"
CACHE = STATE_DIR / "core_cache.json"
SNAPS = STATE_DIR / "prices"
LOGS = ROOT / "logs"
REGULAR_MARKUP = 20.0
DEAL_MARKUP = 15.0
TOP = 40
FEATURED = 8
MIN_MARGIN = 0.05
DEAL_MIN_LISTED_DAYS = 7
DEAL_COOLDOWN_DAYS = 21
CAMPAIGNS = ["", "Black_November_", "Black_Friday_", "Festive_", "Christmas_", "Back_to_School_",
             "Back_to_Business_", "Back_to_Campus_", "Summer_", "Easter_", "Winter_", "Mid_Year_"]
CDN = "https://cdn.shopify.com/s/files/1/0571/8340/4192/files"

LOG_LINES: list[str] = []


def log(msg: str):
    line = f"{dt.datetime.now(SAST):%F %T} {msg}"
    print(line, flush=True)
    LOG_LINES.append(line)


# --------------------------------------------------------------------------- env / rpc

def env() -> dict:
    e = {}
    for f in (Path.home() / ".config/campusplug/app.env", Path.home() / ".config/campusplug/store-sync.env"):
        e.update(core.load_env(f))
    for k in ("CAMPUSPLUG_SUPABASE_URL", "CAMPUSPLUG_SUPABASE_PUBLISHABLE", "STORE_SYNC_TOKEN"):
        if os.environ.get(k):
            e[k] = os.environ[k]
    missing = [k for k in ("CAMPUSPLUG_SUPABASE_URL", "CAMPUSPLUG_SUPABASE_PUBLISHABLE", "STORE_SYNC_TOKEN") if not e.get(k)]
    if missing:
        raise SystemExit(f"missing {missing}")
    return e


def rpc(e: dict, name: str, payload: dict):
    url = e["CAMPUSPLUG_SUPABASE_URL"].rstrip("/") + f"/rest/v1/rpc/{name}"
    key = e["CAMPUSPLUG_SUPABASE_PUBLISHABLE"]
    r = requests.post(url, headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                      json={"p_token": e["STORE_SYNC_TOKEN"], **payload}, timeout=180)
    if r.status_code >= 300:
        raise RuntimeError(f"{name} {r.status_code}: {r.text[:400]}")
    return r.json()


# --------------------------------------------------------------------------- state

def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {}


def save_state(st: dict):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, indent=1, default=str))


def dget(s: str | None) -> dt.date | None:
    return dt.date.fromisoformat(s) if s else None


# --------------------------------------------------------------------------- price list discovery

def list_date(url: str) -> dt.date | None:
    m = re.search(r"/(\d{8})_ARP_Price_list", url or "")
    return dt.datetime.strptime(m.group(1), "%Y%m%d").date() if m else None


def probe_cdn(since: dt.date, today: dt.date) -> str | None:
    """Newest existing YYYYMMDD_ARP_Price_list_*.xlsx after `since` (HEAD requests only)."""
    start = max(since + dt.timedelta(days=1), today - dt.timedelta(days=21))
    d = today
    while d >= start:
        month = d.strftime("%B")
        for camp in CAMPAIGNS:
            for name in (f"{d:%Y%m%d}_ARP_Price_list_{camp}{month}_{d.year}.xlsx",
                         f"{d:%Y%m%d}_ARP_Price_list_{camp}{d.year}.xlsx"):
                try:
                    r = requests.head(f"{CDN}/{name}", timeout=15, headers=core.UA)
                except requests.RequestException:
                    continue
                if r.status_code == 200:
                    return f"{CDN}/{name}"
        d -= dt.timedelta(days=1)
    return None


def discover(st: dict, today: dt.date, probe: bool) -> str | None:
    """Returns a NEWER price list URL than the current one, or None."""
    cur = st.get("pricelist_url")
    cur_d = list_date(cur) or dt.date(2000, 1, 1)
    cands = []
    f = STATE_DIR / "pricelist_url.txt"
    if f.exists() and f.read_text().strip():
        cands.append(f.read_text().strip())
    page = core.find_pricelist_url(None)
    if page:
        cands.append(page)
    if probe and st.get("last_probe") != today.isoformat():
        st["last_probe"] = today.isoformat()
        hit = probe_cdn(cur_d, today)
        if hit:
            cands.append(hit)
    for u in cands:
        if u.split("?")[0] != (cur or "").split("?")[0] and (list_date(u) or dt.date(2000, 1, 1)) >= cur_d:
            return u
    return None


def load_list(url: str) -> bool:
    try:
        p = core.download(url)
        items, eff = core.parse_pricelist(p)
    except Exception as ex:
        log(f"price list {url} unusable: {ex}")
        return False
    if len(items) < 200:
        log(f"price list {url} has only {len(items)} SKUs, ignored")
        return False
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    XLSX.write_bytes(p.read_bytes())
    log(f"NEW price list: {url.split('/')[-1].split('?')[0]} ({len(items)} SKUs, {eff})")
    return True


# --------------------------------------------------------------------------- catalogue

def catalogue(force: bool = False) -> tuple[dict, dict]:
    if CACHE.exists() and not force:
        c = json.loads(CACHE.read_text())
        if dt.datetime.now().timestamp() - c.get("at", 0) < 5 * 3600:
            return c["cat"], c["ranks"]
    try:
        cat = core.fetch_catalogue()
        ranks = core.fetch_bestsellers()
        CACHE.write_text(json.dumps({"at": dt.datetime.now().timestamp(), "cat": cat, "ranks": ranks}))
        return cat, ranks
    except Exception as ex:
        if not CACHE.exists():
            raise
        log(f"Core catalogue fetch failed ({ex}); using cache")
        c = json.loads(CACHE.read_text())
        return c["cat"], c["ranks"]


# --------------------------------------------------------------------------- helpers

def feed_time(today: dt.date, hh=12, mm=30) -> str:
    t = dt.datetime.combine(today, dt.time(hh, mm), SAST)
    now = dt.datetime.now(SAST)
    return (max(t, now + dt.timedelta(minutes=2))).isoformat()


def rand(c: int) -> str:
    return f"R{c // 100:,}".replace(",", " ")


def ptype_of(p: dict) -> str:
    return p.get("product_type") or "Accessories"


# --------------------------------------------------------------------------- main job

def run(mode: str, dry: bool, force_build: bool):
    today = dt.datetime.now(SAST).date()
    st = load_state()
    st.setdefault("listed_since", {})
    st.setdefault("deals", {})
    st.setdefault("deal_history", {})
    alerts, changes = [], []
    e = env()

    # 1. price list
    new_url = discover(st, today, probe=(mode == "sync")) if mode == "sync" else None
    new_list = False
    if new_url and load_list(new_url):
        st["pricelist_url"] = new_url
        st["pricelist_seen"] = today.isoformat()
        new_list = True
        changes.append(f"New Core price list: {new_url.split('/')[-1].split('?')[0]}")
        (STATE_DIR / "pricelist_url.txt").unlink(missing_ok=True)
    if not XLSX.exists():
        raise SystemExit("no price list in state/ yet: put a link in state/pricelist_url.txt")
    items, effective = core.parse_pricelist(XLSX)
    promo_start, promo_end, promo_name = core.promo_window(XLSX)
    by_sku = {i["sku"]: i for i in items}
    cat, ranks = catalogue(force=(mode == "sync"))

    # 2. products (rebuild on a new list, every Monday, or when forced)
    rows = rpc(e, "store_sync_state", {})
    active_core = [r for r in rows if r["status"] == "active"]
    build_due = new_list or force_build or not active_core or (
        today.weekday() == 0 and st.get("last_build") != today.isoformat() and mode == "sync")
    prev = {}
    snaps = sorted(SNAPS.glob("*.json")) if SNAPS.exists() else []
    if snaps:
        prev = json.loads(snaps[-1].read_text()).get("sell_cents", {})
    if build_due:
        products, report = core.build(items, cat, ranks, REGULAR_MARKUP, TOP, prev, promo_end, promo_name)
        log(f"build: {len(products)} products")
        old_handles = {r["handle"] for r in active_core}
        new_handles = [p["handle"] for p in products if p["handle"] not in old_handles]
        if not dry:
            res = rpc(e, "store_sync_import", {"p_actor": "store-sync", "p_products": [
                {**{k: v for k, v in p.items() if not k.startswith("_")},
                 "variants": [{k: v for k, v in var.items() if not k.startswith("_")} for var in p["variants"]]}
                for p in products], "p_status": "active"})
            if res.get("errors"):
                alerts.append(f"import errors: {res['errors'][:3]}")
            rpc(e, "store_sync_apply", {"p_actor": "store-sync", "p_payload": {
                "products": [{"handle": p["handle"], "sort": p["_sort"], "is_featured": p["_sort"] <= FEATURED,
                              "status": "active"} for p in products],
                "archive_core_except": [p["handle"] for p in products],
                "collections": [{"handle": "apple", "sort": 1}, {"handle": "plug-deals", "title": "Plug Deals", "sort": 2},
                                {"handle": "phones", "sort": 10}, {"handle": "laptops-tablets", "sort": 12},
                                {"handle": "audio", "sort": 20}, {"handle": "wearables", "sort": 22},
                                {"handle": "accessories", "sort": 50}]}})
            SNAPS.mkdir(parents=True, exist_ok=True)
            (SNAPS / f"{today}.json").write_text(json.dumps({"pricelist_url": st.get("pricelist_url"), "effective": effective,
                "sell_cents": {i["sku"]: core.sell_cents(i["rrp_incl"], REGULAR_MARKUP) for i in items}}))
        st["last_build"] = today.isoformat()
        for h in new_handles:
            st["listed_since"][h] = today.isoformat()
        if new_handles:
            changes.append(f"{len(new_handles)} products added: " + ", ".join(new_handles[:8]))
        # "just landed": newest NEW-status product among the additions, one post
        fresh = [p for p in products if p["handle"] in new_handles and "new" in p["tags"]]
        if fresh and not dry:
            p = fresh[0]
            lo = min(v["price_cents"] for v in p["variants"])
            rpc(e, "store_sync_apply", {"p_actor": "store-sync", "p_payload": {"feed_posts": [{
                "dedupe_key": f"landed-{p['handle']}", "post_at": feed_time(today, 9, 30), "product_handle": p["handle"],
                "image_url": (p["images"] or [None])[0],
                "content": f"Just landed in the Plug Store: {p['title']} 🍎\n\nFrom {rand(lo)}. Official South African "
                           f"stock from Core, Apple's authorised distributor, with Apple's 1-year warranty.\n\nMarket → Plug Store."}]}})
        rows = rpc(e, "store_sync_state", {}) if not dry else rows
    for r in rows:
        st["listed_since"].setdefault(r["handle"], today.isoformat())

    # 3. guards: Core availability, promo expiry, margin
    drift = 0
    updates: dict[str, dict] = {}
    for r in rows:
        if r["status"] != "active":
            continue
        for v in r["variants"]:
            sku = (v.get("sku") or "").upper()
            it, c = by_sku.get(sku), cat.get(sku, {})
            want, why = True, ""
            if not it:
                want, why = False, "not on Core list"
            elif it["status"] == "EOL":
                want, why = False, "EOL"
            elif c and not c.get("available", True):
                want, why = False, "out of stock at Core"
            elif it["promo"] and promo_end and today > promo_end:
                want, why = False, f"{promo_name} promo ended {promo_end}"
            if it and c.get("price"):
                core_cost = c["price"] * 100
                if abs(c["price"] - it["wholesale_excl"] * 1.15) > 0.02 * it["wholesale_excl"] * 1.15:
                    drift += 1
                if core_cost * (1 + MIN_MARGIN) > v["price_cents"]:
                    want, why = False, f"Core cost R{c['price']:,.0f} leaves < {MIN_MARGIN:.0%} margin"
            if want != v["is_active"]:
                updates.setdefault(r["handle"], {"handle": r["handle"], "variants": []})["variants"].append(
                    {"sku": v["sku"], "is_active": want})
                changes.append(f"{'show' if want else 'hide'} {r['title']} / {v['sku']} {why}")
    if promo_end and today > promo_end and not new_list and st.get("promo_alert") != str(promo_end):
        alerts.append(f"Core's {promo_name} promo ended {promo_end} and no newer price list has been found. "
                      f"Promo SKUs are hidden. Put the new list link in store-sync/state/pricelist_url.txt "
                      f"(core.co.za/pages/arp-arp-price-list-page → Download) or ask Claude to fetch it.")
        st["promo_alert"] = str(promo_end)
    if drift >= 10 and st.get("drift_alert") != today.isoformat():
        alerts.append(f"Core's live wholesale prices differ from the price list on {drift} SKUs: a new list is "
                      f"probably out. Margin guard is on. Send the new Download link.")
        st["drift_alert"] = today.isoformat()

    # 4. deals (15 % markup) driven by the season
    season = season_for(today)
    deals = st["deals"]
    row_by = {r["handle"]: r for r in rows}

    def var_prices(r, markup):
        out = []
        for v in r["variants"]:
            it = by_sku.get((v.get("sku") or "").upper())
            if it:
                reg = core.sell_cents(it["rrp_incl"], REGULAR_MARKUP)
                out.append({"sku": v["sku"], "price_cents": core.sell_cents(it["rrp_incl"], markup) if markup != REGULAR_MARKUP else reg,
                            "compare_at_cents": reg if markup != REGULAR_MARKUP else None})
        return out

    ended = []
    for h, d in list(deals.items()):
        r = row_by.get(h)
        if today > dget(d["end"]) or not r or r["status"] != "active":
            ended.append(h)
            st["deal_history"][h] = today.isoformat()
            del deals[h]
            if r:
                u = updates.setdefault(h, {"handle": h, "variants": []})
                u["variants"] = merge_variants(u["variants"], var_prices(r, REGULAR_MARKUP))
                u["remove_tags"] = ["plug-deal"]
    if ended:
        changes.append("Plug Deals ended: " + ", ".join(ended))
        st["last_round_end"] = today.isoformat()

    started = []
    gap_ok = (today - (dget(st.get("last_round_end")) or dt.date(2000, 1, 1))).days >= season["gap_days"]
    if not deals and gap_ok:
        rnd = random.Random(today.toordinal())
        cands = []
        for r in rows:
            if r["status"] != "active" or not any(v["is_active"] for v in r["variants"]):
                continue
            listed = dget(st["listed_since"].get(r["handle"])) or today
            last = dget(st["deal_history"].get(r["handle"]))
            if (today - listed).days < DEAL_MIN_LISTED_DAYS or (last and (today - last).days < DEAL_COOLDOWN_DAYS):
                continue
            if not var_prices(r, DEAL_MARKUP):
                continue
            w = season["focus"].get(ptype_of(r), 0.6) / math.log((r.get("sort") or 50) + 2)
            if "core-promo" in (r.get("tags") or []):
                w *= 0.6
            cands.append((w * rnd.uniform(0.8, 1.2), r))
        cands.sort(key=lambda x: -x[0])
        per_type: dict[str, int] = {}
        for _, r in cands:
            if len(started) >= season["slots"]:
                break
            t = ptype_of(r)
            if per_type.get(t, 0) >= (1 if season["slots"] <= 5 else 2):
                continue
            per_type[t] = per_type.get(t, 0) + 1
            started.append(r)
        end = today + dt.timedelta(days=season["deal_days"] - 1)
        for r in started:
            deals[r["handle"]] = {"start": today.isoformat(), "end": end.isoformat(), "season": season["name"]}
        if started:
            changes.append(f"Plug Deals started ({season['theme']}, until {end}): " + ", ".join(r["title"] for r in started))
    # (re)apply deal prices to every live deal: a rebuild resets prices to regular
    for h in deals:
        r = row_by.get(h)
        if r:
            u = updates.setdefault(h, {"handle": h, "variants": []})
            u["variants"] = merge_variants(u["variants"], var_prices(r, DEAL_MARKUP))
            u["add_tags"] = ["plug-deal"]

    feed = []
    if started:
        end = dget(deals[started[0]["handle"]]["end"])
        lines = []
        for r in started:
            vp = var_prices(r, DEAL_MARKUP)
            lo = min(vp, key=lambda x: x["price_cents"])
            lines.append(f"• {r['title']}: {rand(lo['price_cents'])} (was {rand(lo['compare_at_cents'])})")
        kick = ""
        if st.get("season_seen") != season["name"]:
            kick = season["kickoff"] + "\n\n"
        cover = (started[0].get("images") or [None])[0]
        feed.append({"dedupe_key": f"deals-{today}", "post_at": feed_time(today, 17, 30), "product_handle": "plug-deals",
                     "image_url": cover,
                     "content": f"{kick}🔥 New Plug Deals, until {end.day} {end:%b}:\n\n" + "\n".join(lines) +
                                "\n\nOfficial SA Apple stock with Apple warranty. Free courier over R1 500. "
                                "Market → Plug Store → Plug Deals."})
    elif st.get("season_seen") != season["name"] and mode == "sync":
        feed.append({"dedupe_key": f"season-{season['name']}-{today.year}", "post_at": feed_time(today),
                     "product_handle": "apple", "content": season["kickoff"] + "\n\nMarket → Plug Store.",
                     "image_url": None})
    st["season_seen"] = season["name"]

    # 5. front card (laptop / phone / tablet), weekly or after a rebuild or when a slot product is hidden
    hero_payload = []
    hero_handles = st.get("hero", [])
    hero_broken = any(h not in row_by or row_by[h]["status"] != "active" for h in hero_handles)
    if mode == "sync" and (build_due or hero_broken or not hero_handles or today.weekday() == 0):
        winners = hero.pick(rows, log=log)
        if len(winners) == 3:
            win = {w["handle"] for w in winners}
            for i, w in enumerate(winners, 1):
                hero_payload.append({"handle": w["handle"], "sort": i, "is_featured": True,
                                     "images": [w["url"]] + [u for u in w["images"] if u != w["url"]]})
            for r in rows:
                if r.get("is_featured") and r["handle"] not in win and (r.get("sort") or 0) <= 3:
                    hero_payload.append({"handle": r["handle"], "sort": (r.get("sort") or 0) + 3})
            st["hero"] = [w["handle"] for w in winners]

    # 6. write
    payload = {"products": list(updates.values()) + hero_payload,
               "collection_members": {"plug-deals": sorted(deals)},
               "collections": [{"handle": "plug-deals", "title": "Plug Deals", "sort": 2, "is_active": bool(deals)}],
               "feed_posts": feed}
    log(f"season {season['name']} slots {season['slots']} | deals live {len(deals)} | updates {len(updates)} | "
        f"hero {len(hero_payload)} | feed {len(feed)} | drift {drift}")
    for c in changes:
        log("  " + c)
    for a in alerts:
        log("  ALERT " + a)
    if dry:
        log("dry run: nothing written")
        print(json.dumps(payload, indent=1, default=str)[:4000])
        return
    res = rpc(e, "store_sync_apply", {"p_actor": "store-sync", "p_payload": payload})
    log(f"applied {res}")
    st["last_run"] = dt.datetime.now(SAST).isoformat(timespec="seconds")
    save_state(st)
    if alerts or (changes and mode == "sync"):
        mail(season, deals, changes, alerts)


def merge_variants(a: list[dict], b: list[dict]) -> list[dict]:
    m = {x["sku"]: dict(x) for x in a}
    for x in b:
        m.setdefault(x["sku"], {}).update(x)
    return list(m.values())


def mail(season, deals, changes, alerts):
    to = os.environ.get("STORE_SYNC_RECIPIENTS") or os.environ.get("STATS_RECIPIENTS") or ""
    to = [a.strip() for a in to.split(",") if a.strip()]
    if not to:
        log("no recipients set; mail skipped")
        return
    sys.path.insert(0, str(Path.home() / "claude-code/qconnec/qconnec-mail"))
    try:
        import cron_sender
    except ImportError:
        log("mailer missing; mail skipped")
        return
    msg = EmailMessage()
    msg["Subject"] = ("⚠️ " if alerts else "") + f"Plug Store × Core sync: {len(changes)} changes, {len(deals)} Plug Deals live"
    msg["From"] = "Campus Plug Store Sync <campusplug.qconnec@gmail.com>"
    msg["To"] = ", ".join(to)
    body = [f"Season: {season['theme']} ({season['slots']} deal slots, {season['deal_days']}-day rounds, "
            f"{season['gap_days']}-day rest{', payday boost' if season.get('payday') else ''})", ""]
    if alerts:
        body += ["ACTION NEEDED"] + [f"- {a}" for a in alerts] + [""]
    body += ["Changes"] + [f"- {c}" for c in changes[:60]]
    body += ["", "Live Plug Deals (15 % markup, was-price = regular 20 % price)"]
    body += [f"- {h} until {d['end']}" for h, d in sorted(deals.items())]
    msg.set_content("\n".join(body))
    try:
        cron_sender.send(msg, log=log)
        log("mail sent")
    except Exception as ex:
        log(f"mail failed: {ex}")


def status():
    st = load_state()
    print(json.dumps({k: st.get(k) for k in ("pricelist_url", "last_build", "last_run", "season_seen", "hero", "deals",
                                             "last_round_end")}, indent=1, default=str))
    print("season today:", season_for(dt.datetime.now(SAST).date()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["sync", "check", "status"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force-build", action="store_true")
    a = ap.parse_args()
    if a.mode == "status":
        return status()
    if (STATE_DIR / "PAUSE").exists():
        log("PAUSE file present: skipped")
        return
    try:
        run(a.mode, a.dry_run, a.force_build)
    except Exception:
        log("FAILED\n" + traceback.format_exc())
        raise
    finally:
        LOGS.mkdir(exist_ok=True)
        with (LOGS / "store-sync.log").open("a") as f:
            f.write("\n".join(LOG_LINES) + "\n")


if __name__ == "__main__":
    main()
