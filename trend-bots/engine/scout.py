"""Agent A - Scout: pull every enabled source, store new items and trend snapshots, log source health."""
from datetime import datetime, timezone

from . import sources


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _record(con, source, ok, n, err):
    con.execute("insert into source_runs (source, run_at, ok, items, error) values (?,?,?,?,?)",
                (source, now_iso(), int(ok), n, err))


def run(cfg, con, log, with_reddit=True):
    http = sources.Http(cfg, log)
    src = cfg["sources"]
    found = new = 0
    seen_now = now_iso()

    batches = []
    for feed in src["news_rss"]:
        items, err = sources.news_rss(http, feed)
        _record(con, feed["name"], err is None and bool(items), len(items), err)
        log(f"  {feed['name']}: {len(items)} items" + (f" ({err})" if err else ""))
        batches.append(items)
    if with_reddit and src["reddit"]["enabled"]:
        items, err = sources.reddit(http, src["reddit"], log)
        _record(con, "Reddit", bool(items), len(items), err)
        log(f"  Reddit: {len(items)} posts" + (f" ({err})" if err else ""))
        batches.append(items)

    for items in batches:
        for it in items:
            found += 1
            pub = it["published_at"].isoformat(timespec="seconds") if it["published_at"] else None
            cur = con.execute(
                """insert or ignore into items (id, source_id, source_name, tier, kind, title, summary,
                                                url, published_at, discovered_at)
                   values (?,?,?,?,?,?,?,?,?,?)""",
                (sources.url_id(it["url"]), it["source_id"], it["source_name"], it["tier"], it["kind"],
                 it["title"], it["summary"], it["url"], pub, seen_now))
            new += cur.rowcount

    trends = 0
    for name, fn in (("Google Trends", sources.google_trends), ("X (Twitter)", sources.x_trends)):
        conf = src["google_trends" if name == "Google Trends" else "x_trends"]
        if not conf.get("enabled"):
            continue
        rows, err = fn(http, conf)
        _record(con, name, err is None and bool(rows), len(rows), err)
        log(f"  {name}: {len(rows)} trends" + (f" ({err})" if err else ""))
        for t in rows:
            con.execute("insert into trend_snapshots (platform, topic, rank, volume, seen_at) values (?,?,?,?,?)",
                        (t["platform"], t["topic"], t["rank"], t["volume"], seen_now))
        trends += len(rows)

    con.commit()
    log(f"scout: {found} items fetched, {new} new, {trends} trend topics")
    return {"found": found, "new": new, "trends": trends}
