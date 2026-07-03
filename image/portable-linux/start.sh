#!/usr/bin/env bash
set -euo pipefail

# Start Cortex from a portable USB layout.
# Cortex will be PID 1 inside the container.

TARGET="${1:-}"
APP_PORT="${APP_PORT:-8080}"
CONTAINER="${CONTAINER:-cortex-usb}"
IMAGE="${IMAGE:-cortex-mvp:usb}"
DRY_RUN="${DRY_RUN:-0}"

if [ -z "$TARGET" ]; then
  echo "usage: $0 /path/to/mounted-usb" >&2
  exit 2
fi

REPO="$TARGET/cortex-mvp"
STATE="$TARGET/state"
ENV_FILE="$TARGET/env/cortex.env"

run() {
  echo "+ $*"
  if [ "$DRY_RUN" != "1" ]; then "$@"; fi
}

if [ "$DRY_RUN" != "1" ] && ! command -v docker >/dev/null 2>&1; then
  echo "refused: docker is required on the host" >&2
  exit 3
fi

run mkdir -p "$STATE/ledger" "$STATE/memory" "$STATE/runtime" "$STATE/data"
run docker build -t "$IMAGE" "$REPO"
run docker rm -f "$CONTAINER"
run docker run -d \
  --name "$CONTAINER" \
  --restart unless-stopped \
  --env-file "$ENV_FILE" \
  -p "127.0.0.1:${APP_PORT}:8080" \
  -v "$STATE:/cortex-state" \
  "$IMAGE"

echo "Cortex started: http://127.0.0.1:${APP_PORT}/pid1"
