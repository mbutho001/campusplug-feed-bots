#!/usr/bin/env python3
"""Campus Plug trend desk - SA news + social trends -> verified engagement posts on the app feed.

  run.py scout                     Agent A only (hourly; builds trend velocity history)
  run.py slot <morning|midday|afternoon|evening> [--dry-run]
                                   scout -> score -> verify + write -> edit -> schedule
  run.py publish                   publish scheduled posts that are due (every 10 min)
  run.py review list | show ID | approve ID | reject ID
  run.py metrics                   pull real engagement for published posts
  run.py report daily|weekly [--dry-run]
  run.py sample                    a dry-run "sample day" (all four slots) -> out/sample-day-<date>.md
"""
import argparse
import html
import json
import sys
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

from engine import analyst, auditor, community, creator, db, editor, growth, publisher, scout

ROOT = Path(__file__).resolve().parent
CFG = json.loads((ROOT / "config.json").read_text())
SAST = ZoneInfo("Africa/Johannesburg")


def log(msg):
    print(f"{datetime.now(SAST):%F %T} {msg}", flush=True)


def today():
    return datetime.now(SAST).date().isoformat()


def do_slot(con, slot, dry_run=False, extra_used=None, run_scout=True):
    if run_scout:
        scout.run(CFG, con, log, with_reddit=True)
    items = analyst.load_recent(con, CFG["rules"]["max_story_age_hours"])
    clusters = analyst.score_clusters(CFG, items, analyst.recent_trends(con),
                                      multipliers={**CFG["category_multipliers"], **growth.multipliers()})
    analyst.dump(clusters[:60], ROOT / "out" / f"clusters-{today()}-{slot}.json")
    used = analyst.recent_fingerprints(con, CFG["rules"]["dedupe_days"]) + (extra_used or [])
    picks = analyst.pick_for_slot(CFG, clusters, slot, used, CFG["candidates_per_slot"])
    log(f"analyst: {len(clusters)} clusters, {sum(not c['rejects'] for c in clusters)} eligible, "
        f"{len(picks)} picked for {slot}")
    if not picks:
        return []
    stories = creator.run(CFG, slot, today(), picks, log)
    if stories is None:
        log("creator failed - nothing generated this slot (no template fallback, by design)")
        return []
    by_key = {c["cluster_key"]: c for c in picks}
    discovered = datetime.now(timezone.utc).isoformat(timespec="seconds")
    auto, results, writers = [], [], {}
    for st in stories:
        c = by_key.get(st.get("cluster_key"))
        if c is None:
            continue
        if st.get("decision") != "publish":
            log(f"  fact-check rejected: {c['headline'][:60]} - {st.get('reject_reason', '')[:100]}")
            results.append(("rejected_by_checker", c["headline"], st.get("reject_reason", "")))
            if extra_used is not None:
                extra_used.append(set(c["fingerprint"].split()))
            continue
        rec = editor.build_record(CFG, c, st, slot, today(), discovered)
        writers[rec["post_id"]] = st.get("_writer")
        head = analyst.tokens(analyst.clean_title(rec["headline"]))
        if any(analyst.same_topic(head, u) for u in used):  # the writer can steer onto an already-covered story
            log(f"  already covered: {rec['headline'][:60]}")
            results.append(("dropped", rec, ["already covered by a recent post (checked after writing)"]))
            continue
        used.append(head)
        errors, reasons = editor.review(CFG, rec)
        rec["bot"] = editor.bot_for(CFG, rec["category"])
        rec["review_reasons"] = reasons
        if errors:
            rec["approval_status"], rec["publication_status"] = "rejected", "rejected"
            log(f"  editor rejected: {rec['headline'][:60]} - {'; '.join(errors)[:160]}")
            if not dry_run:
                db.save_post(con, rec, "rejected", error="; ".join(errors))
            results.append(("rejected", rec, errors))
        else:
            ok, why = (True, "no holds") if not reasons else editor.auto_decide(CFG, rec)
            if ok:
                rec["review_reasons"] = reasons + ([why] if reasons else [])
                auto.append(rec)
            else:
                rec["approval_status"], rec["publication_status"] = "rejected", "rejected"
                log(f"  auto-dropped: {rec['headline'][:60]} - {why}")
                if not dry_run:
                    db.save_post(con, rec, "dropped", error=why)
                results.append(("dropped", rec, [why] + reasons))
    auto.sort(key=lambda r: r["trend_score"], reverse=True)
    quota = CFG["slots"][slot]["publish"]
    shortlist = auto[:quota + 2]  # two spares in case the auditor fails posts
    verdicts = auditor.run(CFG, slot, today(), shortlist, log, writers)
    passed = [r for r in shortlist if verdicts.get(r["post_id"], (False, ""))[0]]
    failed = [r for r in shortlist if r not in passed]
    breaker = shortlist and len(failed) / len(shortlist) >= CFG["auto_policy"]["audit_circuit_breaker"] and len(shortlist) >= 4
    if breaker:
        log(f"auditor failed {len(failed)}/{len(shortlist)} - circuit breaker: nothing published this slot")
        passed = []
        failed = shortlist
    for rec in failed:
        why = verdicts.get(rec["post_id"], (False, "no audit verdict (failed closed)"))[1]
        if breaker:
            why = "circuit breaker: " + why
        rec["approval_status"], rec["publication_status"] = "rejected", "rejected"
        log(f"  audit failed: {rec['headline'][:60]} - {why[:120]}")
        if not dry_run:
            db.save_post(con, rec, "dropped", error="audit: " + why)
        results.append(("audit_failed", rec, [why]))
    keep = passed[:quota]
    for rec, when in zip(keep, editor.slot_times(CFG, slot, len(keep))):
        rec["approval_status"], rec["publication_status"], rec["scheduled_for"] = "auto_approved", "scheduled", when
        rec["audit"] = verdicts[rec["post_id"]][1]
        if not dry_run:
            db.save_post(con, rec, "scheduled", when)
        log(f"  scheduled {when[11:16]}Z [{rec['bot'].split('.')[0]}] {rec['format']}: {rec['headline'][:60]}")
        results.append(("scheduled", rec, []))
    for rec in passed[quota:] + auto[quota + 2:]:
        if not dry_run:
            db.save_post(con, rec, "spare", error="over slot quota")
        results.append(("spare", rec, ["over slot quota"]))
    return results


def do_review(con, args):
    if args.action == "list":
        for r in con.execute("select * from posts where status='needs_review' order by run_date desc, slot"):
            rec = db.load_record(r)
            print(f"{r['id']}\n   {rec['headline']}\n   {rec['risk']} | {rec['verification_status']} "
                  f"{rec['confidence']:.2f} | {', '.join(rec['review_reasons'])}\n")
        return
    row = con.execute("select * from posts where id=?", (args.id,)).fetchone()
    if row is None:
        sys.exit(f"no post {args.id}")
    rec = db.load_record(row)
    if args.action == "show":
        print(editor.feed_text(CFG, rec))
        print("\n--- verification:", rec["verification_status"], rec["confidence"], "\n", rec["verification_notes"])
        print("--- sources:", *[s.get("url") for s in rec["sources"]], sep="\n  ")
    elif args.action == "approve":
        when = editor.slot_times(CFG, rec["slot"], 1)[0] if rec["run_date"] == today() else None
        when = when or datetime.now(timezone.utc).isoformat(timespec="seconds")
        rec["approval_status"], rec["publication_status"], rec["scheduled_for"] = "approved", "scheduled", when
        db.save_post(con, rec, "scheduled", when)
        print(f"approved, scheduled {when}")
    elif args.action == "reject":
        rec["approval_status"], rec["publication_status"] = "rejected", "rejected"
        db.save_post(con, rec, "rejected", error="rejected by reviewer")
        print("rejected")


def source_health(con):
    rows = con.execute("""select source, sum(ok) ok, count(*) runs, max(case when ok then run_at end) last_ok
                          from source_runs where run_at >= datetime('now','-1 day') group by source order by source""")
    return [dict(r) for r in rows]


def send_mail(subject, html_body, text_body, dry_run):
    if dry_run:
        out = ROOT / "out" / f"report-{today()}.html"
        out.write_text(html_body)
        log(f"report (dry run) written to {out}")
        return
    sys.path.insert(0, str(Path.home() / "claude-code" / "qconnec" / "qconnec-mail"))
    from cron_sender import send  # shared Gmail sender for every cron email
    msg = EmailMessage()
    msg["From"] = f"{CFG['report']['from_name']} <campusplug.qconnec@gmail.com>"
    msg["To"] = ", ".join(CFG["report"]["recipients"])
    msg["Subject"] = subject
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    log(f"report sent via {send(msg, log)}")


def do_report(con, kind, dry_run):
    e = html.escape
    if kind == "daily":
        rows = [dict(r) for r in con.execute("select * from posts where run_date=? order by slot, status", (today(),))]
        pub = [db.load_record(r) for r in rows if r["status"] in ("published", "scheduled")]
        rev = []
        rej = [db.load_record(r) | {"err": r["error"]} for r in rows if r["status"] in ("rejected", "dropped")]
        parts = [f"<h2>Campus Plug trend desk - {today()}</h2>",
                 f"<p>{len(pub)} published/scheduled · {len(rej)} dropped automatically. No action needed: "
                 "everything is decided by the automatic policy + independent audit. To stop posting, create "
                 "state/PAUSE in ~/claude-code/campus-plug/campusplug-trend-bots.</p>",
                 "<h3>Published / scheduled</h3><ul>"]
        parts += [f"<li><b>{e(r['headline'])}</b> ({r['slot']}, {r['format']}, {r['bot'].split('.')[0]}, "
                  f"{r.get('publication_status', 'scheduled')})"
                  + (f" - <a href='{e(r['source_url'])}'>{e(r.get('source_outlet', ''))}</a>" if r.get("source_url") else "")
                  + "</li>" for r in pub]
        parts += ["</ul><h3>Dropped automatically (FYI)</h3><ul>"]
        parts += [f"<li>{e(r['headline'])} - {e(r['err'] or '')}</li>" for r in rej]
        parts += ["</ul><h3>Sources (24h)</h3><ul>"]
        parts += [f"<li>{e(s['source'])}: {s['ok']}/{s['runs']} OK</li>" for s in source_health(con)]
        parts += ["</ul><h3>Not connected</h3><ul>"]
        parts += [f"<li>{e(k)}: {e(v)}</li>" for k, v in CFG["sources"]["unavailable"].items()]
        parts.append("</ul>")
        body = "\n".join(parts)
        send_mail(f"Campus Plug trend desk {today()}: {len(pub)} posts", body,
                  f"{len(pub)} posts, {len(rej)} dropped automatically. See HTML version.", dry_run)
    else:
        try:
            growth.collect(con, log)
        except Exception as exc:  # noqa: BLE001 - report the failure, still send what we have
            log(f"growth: metrics fetch failed: {exc}")
        s = growth.summarise(con)
        learned = growth.learn(con, CFG, s, log)
        rows = "".join(f"<tr><td>{e(g)}</td><td>{e(k)}</td><td>{v['posts']}</td><td>{v['avg']}</td></tr>"
                       for g, d in s["groups"].items() for k, v in sorted(d.items(), key=lambda x: -x[1]['avg']))
        body = (f"<h2>Trend desk weekly growth report</h2><p>{s['posts']} posts, {s['interactions']} weighted "
                f"interactions from real (non-bot) users. Impressions, reach, shares and saves are not recorded by "
                f"the app, so they are not reported.</p><table border=1 cellpadding=4><tr><th>group</th><th>value"
                f"</th><th>posts</th><th>avg interactions</th></tr>{rows}</table><p><b>Learning:</b> {e(learned)}</p>")
        send_mail(f"Campus Plug trend desk weekly report {today()}", body, learned, dry_run)


def do_sample(con):
    lines = [f"# Sample day - Campus Plug trend desk ({today()})", "",
             "Dry run: verified + generated, NOT published.", ""]
    used = []
    for i, slot in enumerate(CFG["slots"]):
        results = do_slot(con, slot, dry_run=True, extra_used=used, run_scout=(i == 0))
        used += [set(rec["fingerprint"].split()) for _, rec, _ in results if not isinstance(rec, str)]
        lines += [f"## {CFG['slots'][slot]['label']}", ""]
        for status, rec, why in results:
            if isinstance(rec, str):
                lines += [f"- ~~{rec}~~ - fact-check rejected: {why}", ""]
                continue
            lines += [f"### [{status}] {rec['headline']}",
                      f"*{rec['format']} · {rec['category']} · {rec['bot']} · risk {rec['risk']} · "
                      f"{rec['verification_status']} ({rec['confidence']:.2f}) · trend score {rec['trend_score']}*",
                      "", "```", editor.feed_text(CFG, rec), "```"]
            if why:
                lines += [f"Held because: {'; '.join(why)}"]
            lines += [""]
    out = ROOT / "out" / f"sample-day-{today()}.md"
    out.write_text("\n".join(lines))
    log(f"sample day written to {out}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scout")
    s = sub.add_parser("slot"); s.add_argument("slot", choices=list(CFG["slots"])); s.add_argument("--dry-run", action="store_true")
    sub.add_parser("publish")
    r = sub.add_parser("review"); r.add_argument("action", choices=["list", "show", "approve", "reject"]); r.add_argument("id", nargs="?")
    sub.add_parser("metrics")
    rp = sub.add_parser("report"); rp.add_argument("kind", choices=["daily", "weekly"]); rp.add_argument("--dry-run", action="store_true")
    sub.add_parser("sample")
    cm = sub.add_parser("community"); cm.add_argument("part", choices=["am", "pm"]); cm.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    con = db.connect()
    if args.cmd == "scout":
        scout.run(CFG, con, log, with_reddit=False)
    elif args.cmd == "slot":
        do_slot(con, args.slot, args.dry_run)
    elif args.cmd == "publish":
        n = publisher.publish_due(CFG, con, log)
        if n:
            log(f"publish: {n} posted")
    elif args.cmd == "review":
        if args.action != "list" and not args.id:
            sys.exit("review needs an ID")
        do_review(con, args)
    elif args.cmd == "metrics":
        growth.collect(con, log)
    elif args.cmd == "report":
        do_report(con, args.kind, args.dry_run)
    elif args.cmd == "sample":
        do_sample(con)
    elif args.cmd == "community":
        community.run(CFG, con, args.part, log, args.dry_run)


if __name__ == "__main__":
    main()
