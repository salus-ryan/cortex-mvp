#!/usr/bin/env bash
set -euo pipefail

APP_PORT="${APP_PORT:-8080}"
FORGE_PORT="${FORGE_PORT:-8765}"

probe() {
  local name="$1"
  local url="$2"
  if command -v curl >/dev/null 2>&1 && curl -fsS --max-time 3 "$url" >/dev/null; then
    echo "$name: ok $url"
  else
    echo "$name: unavailable $url"
  fi
}

probe cortex-health "http://127.0.0.1:${APP_PORT}/health"
probe cortex-pid1 "http://127.0.0.1:${APP_PORT}/pid1"
probe forge "http://127.0.0.1:${FORGE_PORT}/forge/status"

if command -v docker >/dev/null 2>&1; then
  docker ps --filter name=cortex-usb --format 'container: {{.Names}} {{.Status}} {{.Ports}}' || true
fi
