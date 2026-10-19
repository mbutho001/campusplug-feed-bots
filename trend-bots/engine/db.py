"""Local SQLite store: discovered items, trend snapshots, source health, posts, experiments.

Only finished posts leave this machine (through the post_engagement RPC); the bulky
discovery data stays local so a production DB change is never needed to tune the engine.
"""
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "state" / "engine.db"

SCHEMA = """
create table if not exists items (
  id            text primary key,          -- sha1 of the canonical URL
  source_id     text not null,
  source_name   text not null,
  tier          int  not null,
  kind          text not null,             -- news | reddit
  title         text not null,
  summary       text,
  url           text not null,
  published_at  text,                      -- ISO UTC, null when the feed gives none
  discovered_at text not null
);
create index if not exists items_disc on items(discovered_at);

create table if not exists trend_snapshots (
  platform  text not null,                 -- Google Trends | X (Twitter)
  topic     text not null,
  rank      int,
  volume    text,
  seen_at   text not null
);
create index if not exists trend_seen on trend_snapshots(seen_at);

create table if not exists source_runs (
  source    text not null,
  run_at    text not null,
  ok        int  not null,
  items     int  not null,
  error     text
);

create table if not exists posts (
  id            text primary key,          -- also the RPC dedupe key
  run_date      text not null,
  slot          text not null,
  cluster_key   text not null,
  fingerprint   text not null,             -- topic words, for cross-day dedupe
  bot           text,
  status        text not null,             -- rejected | needs_review | scheduled | published | failed | expired
  record        text not null,             -- full post JSON (schema/post.schema.json)
  scheduled_for text,
  published_at  text,
  error         text,
  metrics       text
);
create index if not exists posts_status on posts(status, scheduled_for);

create table if not exists experiments (
  id          integer primary key autoincrement,
  started     text not null,
  hypothesis  text not null,
  change      text not null,
  status      text not null default 'running',
  result      text
);
"""


def connect(path=DB_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def save_post(con, rec, status, scheduled_for=None, error=None):
    con.execute(
        """insert into posts (id, run_date, slot, cluster_key, fingerprint, bot, status, record,
                              scheduled_for, error)
           values (?,?,?,?,?,?,?,?,?,?)
           on conflict(id) do update set status=excluded.status, record=excluded.record,
             bot=excluded.bot, scheduled_for=excluded.scheduled_for, error=excluded.error""",
        (rec["post_id"], rec["run_date"], rec["slot"], rec["cluster_key"], rec["fingerprint"],
         rec.get("bot"), status, json.dumps(rec, ensure_ascii=False), scheduled_for, error))
    con.commit()


def load_record(row):
    return json.loads(row["record"])
