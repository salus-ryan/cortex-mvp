#!/usr/bin/env bash
set -euo pipefail

APP_NAME="${APP_NAME:-cortex}"
IMAGE_NAME="${IMAGE_NAME:-cortex-mvp:forge}"
CONTAINER_NAME="${CONTAINER_NAME:-cortex}"
PORT="${PORT:-8080}"
HOST_PORT="${HOST_PORT:-8080}"
PUBLIC_URL="${PUBLIC_URL:-http://127.0.0.1:${HOST_PORT}}"
WITNESS="${WITNESS:-}"
CONFIRMED="${CONFIRMED:-false}"
DATA_ROOT="${DATA_ROOT:-/var/lib/cortex}"
LOG_DIR="${LOG_DIR:-/var/log/cortex-forge}"
ENV_FILE="${ENV_FILE:-/etc/cortex/env}"

if [ "$CONFIRMED" != "true" ]; then
  echo "refused: CONFIRMED=true is required" >&2
  exit 2
fi
if [ -z "$WITNESS" ]; then
  echo "refused: WITNESS is required" >&2
  exit 2
fi
if ! command -v docker >/dev/null 2>&1; then
  echo "refused: docker is required" >&2
  exit 2
fi

mkdir -p "$LOG_DIR"
commit="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
timestamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
log_file="$LOG_DIR/deploy-${timestamp}.json"
previous=""
if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
  previous="${CONTAINER_NAME}-previous-${timestamp}"
  docker rename "$CONTAINER_NAME" "$previous"
fi

cleanup_restore() {
  code=$?
  if [ $code -ne 0 ]; then
    echo "deploy failed; attempting rollback" >&2
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
    if [ -n "$previous" ]; then
      docker rename "$previous" "$CONTAINER_NAME" >/dev/null 2>&1 || true
      docker start "$CONTAINER_NAME" >/dev/null 2>&1 || true
    fi
  fi
  exit $code
}
trap cleanup_restore EXIT

docker build -t "$IMAGE_NAME" .

args=(
  run -d
  --name "$CONTAINER_NAME"
  --restart unless-stopped
  -p "${HOST_PORT}:${PORT}"
  -e PORT="$PORT"
  -e CORTEX_ROOT=/app
  -v "$DATA_ROOT/ledger:/app/ledger"
  -v "$DATA_ROOT/runtime:/app/runtime"
  -v "$DATA_ROOT/memory:/app/memory"
  -v "$DATA_ROOT/data:/app/data"
)
if [ -f "$ENV_FILE" ]; then
  args+=(--env-file "$ENV_FILE")
fi
args+=("$IMAGE_NAME")

docker "${args[@]}"
PUBLIC_URL="$PUBLIC_URL" "$(dirname "$0")/healthcheck.sh"

if [ -n "$previous" ]; then
  docker rm -f "$previous" >/dev/null 2>&1 || true
fi
trap - EXIT

cat > "$log_file" <<JSON
{
  "status": "deployed",
  "timestamp": "$timestamp",
  "app": "$APP_NAME",
  "commit": "$commit",
  "image": "$IMAGE_NAME",
  "container": "$CONTAINER_NAME",
  "public_url": "$PUBLIC_URL",
  "witness": "$WITNESS",
  "confirmed": true
}
JSON

cat "$log_file"
echo "forge deploy: pass"
