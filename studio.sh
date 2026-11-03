#!/usr/bin/env bash
# Run Shortcut Studio in the background on a server (Linux or macOS).
#   ./studio.sh start | stop | restart | status | update
# Host, port and keys come from backend/.env. `start` does nothing if it is already running,
# so it is safe to call from cron to bring the app back after a crash or reboot.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
PID_FILE="$BACKEND/data/studio.pid"
LOG_FILE="$BACKEND/data/studio.log"
mkdir -p "$BACKEND/data"

running() { [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; }

start() {
  if running; then echo "Already running (pid $(cat "$PID_FILE"))."; return; fi
  cd "$BACKEND"
  nohup .venv/bin/python -m app >> "$LOG_FILE" 2>&1 &
  echo $! > "$PID_FILE"
  sleep 3
  if running; then echo "Started (pid $(cat "$PID_FILE")). Log: $LOG_FILE"
  else echo "Failed to start; last log lines:"; tail -20 "$LOG_FILE"; exit 1; fi
}

stop() {
  if running; then kill "$(cat "$PID_FILE")"; sleep 4; running && kill -9 "$(cat "$PID_FILE")" || true; echo "Stopped."
  else echo "Not running."; fi
  rm -f "$PID_FILE"
}

update() {
  cd "$ROOT"
  git pull --ff-only
  "$BACKEND/.venv/bin/pip" install -q -r "$BACKEND/requirements.txt"
  if command -v npm >/dev/null; then (cd frontend && npm ci --no-audit --no-fund && npm run build)
  else echo "npm not found: build the frontend elsewhere and upload frontend/dist."; fi
  stop; start
}

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  restart) stop; start ;;
  status) if running; then echo "Running (pid $(cat "$PID_FILE"))."; else echo "Not running."; fi ;;
  update) update ;;
  *) echo "Usage: $0 start|stop|restart|status|update"; exit 1 ;;
esac
