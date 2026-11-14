#!/usr/bin/env bash
# Run Shortcut Studio in the background on a server (Linux or macOS).
#   ./studio.sh start | stop | restart | status | update | searxng | ffmpeg
# Host, port and keys come from backend/.env. `start` does nothing if it is already running,
# so it is safe to call from cron to bring the app back after a crash or reboot.
# `searxng` installs (or updates) a private SearXNG search engine for Video Maker in backend/data/searxng,
# with no sudo or Docker; from then on start, stop, restart, status and update look after it too.
# The whole script is read before it runs (the braces), so `update` can safely pull a new copy of it.
{
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
PID_FILE="$BACKEND/data/studio.pid"
LOG_FILE="$BACKEND/data/studio.log"
ENV_FILE="$BACKEND/.env"
SEARX_DIR="$BACKEND/data/searxng"
SEARX_SRC="$SEARX_DIR/src"
SEARX_PY="$SEARX_DIR/.venv/bin/python"
SEARX_SETTINGS="$SEARX_DIR/settings.yml"
SEARX_PID="$SEARX_DIR/searxng.pid"
SEARX_LOG="$SEARX_DIR/searxng.log"
SEARX_REPO="https://github.com/searxng/searxng.git"
mkdir -p "$BACKEND/data"

running() { [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; }

start() {
  searx_start
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
  searx_stop
}

# ffmpeg and ffprobe: on PATH, set in backend/.env, or a standalone (static) build downloaded into
# backend/data/bin on Linux servers that have none (no sudo needed); the app looks there too.
# Whether a tool is usable: the file named in backend/.env ($2), one on PATH, or one in backend/data/bin.
have_tool() {
  local setting
  setting="$(sed -n "s/^$2=//p" "$ENV_FILE" 2>/dev/null | tail -1 | tr -d "\"'")"
  [[ -n "$setting" && -x "$setting" ]] && return 0
  command -v "$1" >/dev/null && return 0
  [[ -x "$BACKEND/data/bin/$1" ]]
}

ensure_ffmpeg() {
  local bin="$BACKEND/data/bin"
  if have_tool ffmpeg SHORTCUT_FFMPEG && have_tool ffprobe SHORTCUT_FFPROBE; then return 0; fi
  if [[ "$(uname -s)" != Linux ]]; then echo "ffmpeg not found: install it (macOS: brew install ffmpeg)."; return 0; fi
  local arch; case "$(uname -m)" in x86_64) arch=amd64 ;; aarch64|arm64) arch=arm64 ;;
    *) echo "ffmpeg not found and no standalone build for $(uname -m); install ffmpeg."; return 0 ;; esac
  echo "ffmpeg not found; downloading a standalone build into $bin..."
  mkdir -p "$bin"
  local archive="$bin/ffmpeg-static.tar.xz" url="https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-$arch-static.tar.xz"
  if ! { if command -v curl >/dev/null; then curl -fsSL --retry 2 -o "$archive" "$url"; else wget -qO "$archive" "$url"; fi; }; then
    rm -f "$archive"; echo "Downloading ffmpeg failed; install it or set SHORTCUT_FFMPEG and SHORTCUT_FFPROBE."; return 0
  fi
  # Python's tarfile reads .xz even where tar has no xz support.
  "$BACKEND/.venv/bin/python" - "$archive" "$bin" <<'PY'
import os, sys, tarfile
archive, bin_dir = sys.argv[1], sys.argv[2]
with tarfile.open(archive) as tar:
    for member in tar.getmembers():
        name = os.path.basename(member.name)
        if member.isfile() and name in ("ffmpeg", "ffprobe"):
            with tar.extractfile(member) as src, open(os.path.join(bin_dir, name), "wb") as out:
                out.write(src.read())
            os.chmod(os.path.join(bin_dir, name), 0o755)
PY
  rm -f "$archive"
  echo "Installed $("$bin/ffmpeg" -hide_banner -version | head -1)."
  if grep -q '^SHORTCUT_FF' "$ENV_FILE" 2>/dev/null; then
    echo "Note: SHORTCUT_FFMPEG/SHORTCUT_FFPROBE in backend/.env point to missing files and are ignored; you can delete them."
  fi
}

update() {
  cd "$ROOT"
  git pull --ff-only
  "$BACKEND/.venv/bin/pip" install -q -r "$BACKEND/requirements.txt"
  if command -v npm >/dev/null; then (cd frontend && npm ci --no-audit --no-fund && npm run build)
  else echo "npm not found: build the frontend elsewhere and upload frontend/dist."; fi
  ensure_ffmpeg
  if searx_installed; then searx_install; fi
  stop; start
}

# --- SearXNG (optional metasearch for Video Maker) ------------------------------------------

searx_installed() { [[ -x "$SEARX_PY" && -d "$SEARX_SRC/searx" && -f "$SEARX_SETTINGS" ]]; }
searx_running() { [[ -f "$SEARX_PID" ]] && kill -0 "$(cat "$SEARX_PID")" 2>/dev/null; }
searx_port() { sed -n 's/^  port: *\([0-9][0-9]*\).*/\1/p' "$SEARX_SETTINGS" | head -1; }

# Answers on its port within $1 seconds (0: right now).
searx_answers() {
  "$SEARX_PY" - "$(searx_port)" "${1:-0}" <<'PY'
import sys, time, urllib.request
port, wait = sys.argv[1], float(sys.argv[2])
end = time.time() + wait
while True:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=3)
        sys.exit(0)
    except Exception:
        if time.time() >= end:
            sys.exit(1)
        time.sleep(1)
PY
}

# A Python new enough for SearXNG (3.11+): SEARXNG_PYTHON, one on the PATH, or a private copy
# downloaded with uv into backend/data/searxng (no sudo needed).
searx_python() {
  local py
  for py in ${SEARXNG_PYTHON:-} python3.13 python3.12 python3.11 python3; do
    if command -v "$py" >/dev/null && "$py" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
      command -v "$py"; return
    fi
  done
  local uv="$SEARX_DIR/uv/uv"
  export UV_PYTHON_INSTALL_DIR="$SEARX_DIR/python"
  if [[ ! -x "$uv" ]]; then
    echo "No Python 3.11+ found; downloading a private copy for SearXNG (into $SEARX_DIR)..." >&2
    if command -v curl >/dev/null; then curl -LsSf https://astral.sh/uv/install.sh
    elif command -v wget >/dev/null; then wget -qO- https://astral.sh/uv/install.sh
    else echo "Neither curl nor wget is available to download Python." >&2; exit 1
    fi | env UV_INSTALL_DIR="$SEARX_DIR/uv" UV_NO_MODIFY_PATH=1 sh >&2
  fi
  "$uv" python install -q 3.12 >&2
  "$uv" python find 3.12
}

searx_install() {
  mkdir -p "$SEARX_DIR"
  local py
  # A venv made with an older Python (SearXNG now needs 3.11) is rebuilt.
  if [[ -x "$SEARX_PY" ]] && ! "$SEARX_PY" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
    rm -rf "$SEARX_DIR/.venv"
  fi
  if [[ ! -x "$SEARX_PY" ]]; then py="$(searx_python)"; echo "Using $py ($("$py" --version 2>&1))."; fi
  if [[ -d "$SEARX_SRC/.git" ]]; then echo "Updating SearXNG..."; git -C "$SEARX_SRC" pull -q --ff-only
  else echo "Downloading SearXNG..."; git clone -q --depth 1 "$SEARX_REPO" "$SEARX_SRC"; fi
  [[ -x "$SEARX_PY" ]] || "$py" -m venv "$SEARX_DIR/.venv"
  "$SEARX_PY" -c 'import sys; sys.exit(sys.version_info < (3, 11))' || { echo "SearXNG's Python is too old."; exit 1; }
  echo "Installing SearXNG's packages (a minute or two the first time)..."
  { "$SEARX_PY" -m pip install -U pip setuptools wheel &&
    "$SEARX_PY" -m pip install -r "$SEARX_SRC/requirements.txt" &&
    "$SEARX_PY" -m pip install --no-build-isolation -e "$SEARX_SRC"; } >> "$SEARX_LOG" 2>&1 ||
    { echo "Installing SearXNG failed; last log lines:"; tail -20 "$SEARX_LOG"; exit 1; }
  if [[ ! -f "$SEARX_SETTINGS" ]]; then
    # A private instance: only this server can reach it, it answers in JSON and has no rate limiter.
    "$SEARX_PY" - "$SEARX_SETTINGS" <<'PY'
import secrets, socket, sys
port = next(p for p in range(8888, 8999) if socket.socket().connect_ex(("127.0.0.1", p)) != 0)
open(sys.argv[1], "w").write(f"""use_default_settings: true
server:
  bind_address: "127.0.0.1"
  port: {port}
  secret_key: "{secrets.token_hex(32)}"
  limiter: false
  public_instance: false
search:
  formats: [html, json]
""")
PY
  fi
  # Tell Shortcut Studio where it is (replacing any earlier SEARXNG_URL line).
  "$SEARX_PY" - "$ENV_FILE" "http://127.0.0.1:$(searx_port)" <<'PY'
import os, sys
path, url = sys.argv[1], sys.argv[2]
lines = open(path).read().splitlines() if os.path.exists(path) else []
lines = [l for l in lines if not l.strip().lstrip("# ").startswith("SEARXNG_URL=")] + [f"SEARXNG_URL={url}"]
open(path, "w").write("\n".join(lines) + "\n")
PY
  echo "SearXNG installed on port $(searx_port); SEARXNG_URL set in backend/.env."
}

searx_start() {
  searx_installed || return 0
  if searx_running; then return 0; fi
  cd "$SEARX_SRC"
  SEARXNG_SETTINGS_PATH="$SEARX_SETTINGS" nohup "$SEARX_PY" -m searx.webapp >> "$SEARX_LOG" 2>&1 &
  echo $! > "$SEARX_PID"
  if searx_answers 30; then echo "SearXNG started on port $(searx_port) (pid $(cat "$SEARX_PID"))."
  else echo "SearXNG did not start; last log lines:"; tail -20 "$SEARX_LOG"; searx_stop >/dev/null; fi
}

searx_stop() {
  if searx_running; then kill "$(cat "$SEARX_PID")"; sleep 2; searx_running && kill -9 "$(cat "$SEARX_PID")" || true
    echo "SearXNG stopped."; fi
  rm -f "$SEARX_PID"
}

searxng() {
  searx_install
  stop >/dev/null  # the app reads SEARXNG_URL when it starts; SearXNG restarts on the new code
  start
  searx_answers 0 || { echo "SearXNG is not answering; see $SEARX_LOG"; exit 1; }
  echo "Done: in Video Maker, tick SearXNG images or SearXNG video."
}

status() {
  if running; then echo "Running (pid $(cat "$PID_FILE"))."; else echo "Not running."; fi
  if searx_installed; then
    if searx_running; then echo "SearXNG running on port $(searx_port) (pid $(cat "$SEARX_PID"))."
    else echo "SearXNG installed but not running."; fi
  fi
}

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  restart) stop; start ;;
  status) status ;;
  update) update ;;
  searxng) searxng ;;
  ffmpeg) ensure_ffmpeg; if running; then stop >/dev/null; start; fi ;;
  *) echo "Usage: $0 start|stop|restart|status|update|searxng|ffmpeg"; exit 1 ;;
esac
exit
}
