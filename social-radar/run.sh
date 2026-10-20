#!/usr/bin/env bash
# Campus Plug Social Radar — cron entrypoint. Runs daily at 12:00 SAST.
# Single-instance guarded: a slow run (Reddit backs us off hard) never overlaps
# the next one.
set -uo pipefail
cd "$(dirname "$0")" || exit 1

LOG="logs/cron.log"
mkdir -p logs state cache

exec 9>state/run.lock
if ! flock -n 9; then
  echo "$(date '+%F %T') skipped: previous run still going" >> "$LOG"
  exit 0
fi

echo "$(date '+%F %T') --- starting ---" >> "$LOG"
timeout 55m python3 agent.py >> "$LOG" 2>&1
status=$?
echo "$(date '+%F %T') --- finished (exit $status) ---" >> "$LOG"

# Keep the log from growing without bound.
tail -n 4000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
exit $status
