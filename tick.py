#!/usr/bin/env python3
"""One scheduler tick for the Campus Plug feed bots on GitHub Actions.

The workflow fires every 10 minutes (GitHub may start it late or skip one), so this
script works out which jobs are due instead of trusting the cron minute. The times
are the old crontab on the home PC, in SAST:

  06:15 community am    06:30 opportunities collect + morning slot    11:30 midday slot
  12:45 community pm    15:00 afternoon slot    18:30 evening slot    21:30 daily report
  Mon 08:00 weekly report
  scout hourly 06-22, opportunity bots (2 listings) hourly 07-21, publish every tick 07-22

A daily job runs once on the first tick after its time and before its deadline, so a
late tick catches up and a missed morning never fires at night. A job is marked as
attempted even when it fails, so a broken step never re-runs every 10 minutes.

  python3 tick.py            run whatever is due
  python3 tick.py --list     show what would run now, run nothing
  python3 tick.py --only publish scout   run just these jobs (manual trigger)
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

SAST = ZoneInfo("Africa/Johannesburg")
ROOT = Path(__file__).resolve().parent
TREND = ROOT / "trend-bots"
OPP = ROOT / "opportunities"
STATE = ROOT / "tick-state.json"

PY = sys.executable
TREND_RUN = [PY, "run.py"]

# name: (start HH:MM, deadline HH:MM, weekday or None, cwd, [commands], timeout minutes)
DAILY = {
    "community-am":   ("06:15", "12:00", None, TREND, [TREND_RUN + ["community", "am"]], 30),
    "opp-collect":    ("06:30", "21:00", None, OPP, [[PY, "scripts/collect.py"], [PY, "scripts/posts.py"]], 25),
    "slot-morning":   ("06:30", "11:00", None, TREND, [TREND_RUN + ["slot", "morning"]], 40),
    "slot-midday":    ("11:30", "15:00", None, TREND, [TREND_RUN + ["slot", "midday"]], 40),
    "community-pm":   ("12:45", "18:00", None, TREND, [TREND_RUN + ["community", "pm"]], 30),
    "slot-afternoon": ("15:00", "18:00", None, TREND, [TREND_RUN + ["slot", "afternoon"]], 40),
    "slot-evening":   ("18:30", "21:30", None, TREND, [TREND_RUN + ["slot", "evening"]], 40),
    "report-daily":   ("21:30", "23:59", None, TREND, [TREND_RUN + ["report", "daily"]], 10),
    "report-weekly":  ("08:00", "23:59", 0, TREND, [TREND_RUN + ["report", "weekly"]], 10),
}

# name: (first hour, last hour, minimum minutes between runs, cwd, command, timeout minutes)
REPEAT = {
    "scout":       (6, 22, 55, TREND, TREND_RUN + ["scout"], 20),
    "opp-publish": (7, 21, 55, OPP, [PY, "scripts/app_publish.py", "--all", "--max", "2"], 10),
    "publish":     (7, 22, 0, TREND, TREND_RUN + ["publish"], 10),
}

# Each job logs where its cron line used to (logs/<job>.log), so the logs read the same.
LOG_NAME = {"opp-collect": "run", "opp-publish": "app_publish", "publish": "publish", "scout": "scout",
            "report-daily": "report", "report-weekly": "report"}


def hm(text):
    h, m = text.split(":")
    return int(h), int(m)


def due_jobs(now, state):
    today = now.date().isoformat()
    jobs = []
    for name, (start, deadline, weekday, cwd, cmds, limit) in DAILY.items():
        if weekday is not None and now.weekday() != weekday:
            continue
        if not hm(start) <= (now.hour, now.minute) <= hm(deadline):
            continue
        if state.get(name) == today:
            continue
        jobs.append((name, cwd, cmds, limit))
    for name, (first, last, gap, cwd, cmd, limit) in REPEAT.items():
        if not first <= now.hour <= last:
            continue
        prev = state.get(name)
        if gap and prev and now - datetime.fromisoformat(prev) < timedelta(minutes=gap):
            continue
        jobs.append((name, cwd, [cmd], limit))
    return jobs


def log_path(name, cwd):
    stem = LOG_NAME.get(name) or name.split("-", 1)[0]
    path = cwd / "logs" / f"{stem}.log"
    path.parent.mkdir(exist_ok=True)
    return path


def run_job(name, cwd, cmds, limit):
    log = log_path(name, cwd)
    status = 0
    with log.open("a") as fh:
        for cmd in cmds:
            try:
                status = subprocess.run(cmd, cwd=cwd, stdout=fh, stderr=subprocess.STDOUT,
                                        timeout=limit * 60).returncode
            except subprocess.TimeoutExpired:
                status = 124
            if status:
                fh.write(f"{datetime.now(SAST):%F %T} {' '.join(cmd[1:])} exited {status}\n")
                break
    lines = log.read_text(errors="replace").splitlines()[-5000:]
    log.write_text("\n".join(lines) + "\n")
    return status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args()

    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    now = datetime.now(SAST).replace(tzinfo=None)
    if args.only:
        table = {n: (v[3], v[4], v[5]) for n, v in DAILY.items()}
        table.update({n: (v[3], [v[4]], v[5]) for n, v in REPEAT.items()})
        unknown = set(args.only) - set(table)
        if unknown:
            sys.exit(f"unknown job(s): {', '.join(sorted(unknown))}; known: {', '.join(table)}")
        jobs = [(n, *table[n]) for n in args.only]
    else:
        jobs = due_jobs(now, state)

    print(f"{now:%F %T} SAST due: {', '.join(j[0] for j in jobs) or 'nothing'}", flush=True)
    if args.list:
        return
    failed = []
    for name, cwd, cmds, limit in jobs:
        status = run_job(name, cwd, cmds, limit)
        state[name] = now.date().isoformat() if name in DAILY else now.isoformat(timespec="seconds")
        STATE.write_text(json.dumps(state, indent=2) + "\n")
        print(f"  {name}: {'ok' if status == 0 else f'exit {status}'}", flush=True)
        if status:
            failed.append(name)
    # One failing job must not block the state push, so this only reports.
    if failed:
        print(f"::warning::failed: {', '.join(failed)} (see the job logs in the state repo)")


if __name__ == "__main__":
    main()
