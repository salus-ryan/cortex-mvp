#!/usr/bin/env bash
set -euo pipefail

# Start local Cortex Forge dashboard and open it in a browser.
# Usage:
#   scripts/open_cortex.sh
#
# Optional:
#   START_CORTEX=1 scripts/open_cortex.sh   # also starts Cortex container if Docker exists
#   NO_BROWSER=1 scripts/open_cortex.sh     # start only
#   DRY_RUN=1 scripts/open_cortex.sh        # print actions

HOST="${FORGE_HOST:-127.0.0.1}"
PORT="${FORGE_PORT:-8765}"
URL="${CORTEX_URL:-http://${HOST}:${PORT}/ui}"
START_CORTEX="${START_CORTEX:-0}"
NO_BROWSER="${NO_BROWSER:-0}"
DRY_RUN="${DRY_RUN:-0}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/cortex"
PID_FILE="$CACHE/forge-dashboard.pid"
LOG_FILE="$CACHE/forge-dashboard.log"
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3 || command -v python)"

run() {
  echo "+ $*"
  if [ "$DRY_RUN" != "1" ]; then "$@"; fi
}

is_running() {
  [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

open_url() {
  local url="$1"
  if [ "$NO_BROWSER" = "1" ]; then
    echo "browser skipped: $url"
    return 0
  fi
  if command -v termux-open-url >/dev/null 2>&1; then run termux-open-url "$url"
  elif command -v xdg-open >/dev/null 2>&1; then run xdg-open "$url"
  elif command -v open >/dev/null 2>&1; then run open "$url"
  else run "$PY" -m webbrowser "$url"
  fi
}

start_forge() {
  run mkdir -p "$CACHE" "$REPO/.forge-state"
  if is_running; then
    echo "Forge dashboard already running: pid $(cat "$PID_FILE")"
    return 0
  fi
  echo "+ start Forge dashboard on $HOST:$PORT"
  if [ "$DRY_RUN" != "1" ]; then
    (
      cd "$REPO"
      FORGE_ROOT="$REPO/.forge-state" \
      FORGE_REPO="$REPO" \
      FORGE_HOST="$HOST" \
      FORGE_PORT="$PORT" \
      "$PY" -m cortex_forge.server
    ) >"$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
  fi
}

start_cortex_container() {
  if [ "$START_CORTEX" != "1" ]; then return 0; fi
  if ! command -v docker >/dev/null 2>&1; then
    echo "warning: START_CORTEX=1 requested but Docker is unavailable" >&2
    return 0
  fi
  run docker build -t cortex-mvp:local "$REPO"
  run docker rm -f cortex-local
  run docker run -d --name cortex-local --restart unless-stopped -e CORTEX_PROFILE=compact -p 127.0.0.1:8080:8080 cortex-mvp:local
}

wait_for_forge() {
  if [ "$DRY_RUN" = "1" ]; then return 0; fi
  if command -v curl >/dev/null 2>&1; then
    for _ in $(seq 1 30); do
      if curl -fsS "http://${HOST}:${PORT}/forge/status" >/dev/null 2>&1; then return 0; fi
      sleep 0.2
    done
    echo "warning: Forge did not answer yet; see $LOG_FILE" >&2
  fi
}

start_forge
start_cortex_container
wait_for_forge
open_url "$URL"
echo "Cortex Forge dashboard: $URL"
echo "log: $LOG_FILE"
echo "stop: kill \$(cat $PID_FILE)"
