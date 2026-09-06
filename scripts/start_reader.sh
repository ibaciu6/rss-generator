#!/usr/bin/env bash
# Launch the local Inoreader-style feed reader as a background task.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
PORT=${1:-8080}
LOG=/tmp/rss-reader.log

pkill -f "scripts/local_reader.py" 2>/dev/null || true
setsid nohup python3 "$ROOT/scripts/local_reader.py" "$PORT" --no-browser \
  >>"$LOG" 2>&1 < /dev/null &

sleep 1

if curl -s -o /dev/null "http://localhost:$PORT/reader"; then
  echo "Reader running at http://localhost:$PORT/reader"
  echo "Log: $LOG"
else
  echo "Failed to start. Check log: $LOG"
  tail -5 "$LOG"
  exit 1
fi