"""Seen-item store so the daily digest never repeats content."""
import hashlib
import sqlite3
import time
from pathlib import Path

DB = Path(__file__).resolve().parent / "state" / "seen.sqlite3"
KEEP_DAYS = 21


def item_id(platform, key):
    return hashlib.sha1(f"{platform}|{key}".encode("utf-8", "replace")).hexdigest()


def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.execute("CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY, platform TEXT, "
                "label TEXT, first_seen REAL)")
    con.execute("CREATE TABLE IF NOT EXISTS runs (started REAL, sent INTEGER, note TEXT)")
    con.commit()
    return con


def is_new(con, iid):
    return con.execute("SELECT 1 FROM seen WHERE id=?", (iid,)).fetchone() is None


def mark(con, iid, platform, label):
    con.execute("INSERT OR IGNORE INTO seen VALUES (?,?,?,?)",
                (iid, platform, label[:200], time.time()))


def record_run(con, sent, note):
    con.execute("INSERT INTO runs VALUES (?,?,?)", (time.time(), sent, note))


def prune(con):
    con.execute("DELETE FROM seen WHERE first_seen < ?", (time.time() - KEEP_DAYS * 86400,))
    con.commit()
