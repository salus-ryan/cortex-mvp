#!/usr/bin/env bash
set -euo pipefail

CONTAINER_NAME="${CONTAINER_NAME:-cortex}"
LOG_DIR="${LOG_DIR:-/var/log/cortex-forge}"
mkdir -p "$LOG_DIR"

timestamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
previous="$(docker ps -a --format '{{.Names}}' | grep "^${CONTAINER_NAME}-previous-" | sort | tail -1 || true)"

if [ -z "$previous" ]; then
  echo "refused: no previous container found" >&2
  exit 2
fi

docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
docker rename "$previous" "$CONTAINER_NAME"
docker start "$CONTAINER_NAME" >/dev/null

cat > "$LOG_DIR/rollback-${timestamp}.json" <<JSON
{
  "status": "rolled_back",
  "timestamp": "$timestamp",
  "container": "$CONTAINER_NAME",
  "restored": "$previous"
}
JSON

cat "$LOG_DIR/rollback-${timestamp}.json"
echo "forge rollback: pass"
