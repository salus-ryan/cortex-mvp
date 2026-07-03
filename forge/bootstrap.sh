#!/usr/bin/env bash
set -euo pipefail

# One-command Cortex Forge host bootstrap.
# Intended for a fresh Ubuntu/Debian VPS. Safe to re-run.
#
# Usage:
#   sudo DOMAIN=cortex.example.com REPO_URL=https://github.com/salus-ryan/cortex-mvp.git FORGE_TOKEN=... forge/bootstrap.sh
#
# Dry run:
#   DRY_RUN=1 forge/bootstrap.sh

APP_NAME="${APP_NAME:-cortex}"
DOMAIN="${DOMAIN:-}"
REPO_URL="${REPO_URL:-https://github.com/salus-ryan/cortex-mvp.git}"
REPO_DIR="${REPO_DIR:-/opt/cortex-mvp}"
FORGE_ROOT="${FORGE_ROOT:-/var/lib/cortex-forge}"
DATA_ROOT="${DATA_ROOT:-/var/lib/cortex}"
LOG_DIR="${LOG_DIR:-/var/log/cortex-forge}"
ENV_DIR="${ENV_DIR:-/etc/cortex}"
FORGE_PORT="${FORGE_PORT:-8765}"
APP_PORT="${APP_PORT:-8080}"
HOST_PORT="${HOST_PORT:-8080}"
FORGE_TOKEN="${FORGE_TOKEN:-}"
PUBLIC_URL="${PUBLIC_URL:-}"
DRY_RUN="${DRY_RUN:-0}"

if [ -z "$PUBLIC_URL" ]; then
  if [ -n "$DOMAIN" ]; then PUBLIC_URL="https://${DOMAIN}"; else PUBLIC_URL="http://127.0.0.1:${HOST_PORT}"; fi
fi

run() {
  echo "+ $*"
  if [ "$DRY_RUN" != "1" ]; then "$@"; fi
}

write_file() {
  local path="$1"
  echo "+ write $path"
  if [ "$DRY_RUN" = "1" ]; then cat >/dev/null; return 0; fi
  mkdir -p "$(dirname "$path")"
  cat > "$path"
}

need_root() {
  if [ "${EUID:-$(id -u)}" -ne 0 ] && [ "$DRY_RUN" != "1" ]; then
    echo "refused: run as root or with sudo" >&2
    exit 2
  fi
}

install_packages() {
  if command -v apt-get >/dev/null 2>&1; then
    run apt-get update
    run apt-get install -y --no-install-recommends git curl ca-certificates python3 python3-venv python3-pip docker.io caddy
    run systemctl enable --now docker
  else
    echo "warning: apt-get not found; install git docker caddy python3 manually" >&2
  fi
}

clone_or_update_repo() {
  if [ -d "$REPO_DIR/.git" ]; then
    run git -C "$REPO_DIR" pull --ff-only
  else
    run mkdir -p "$(dirname "$REPO_DIR")"
    run git clone "$REPO_URL" "$REPO_DIR"
  fi
  run chmod +x "$REPO_DIR"/forge/*.sh
}

write_config() {
  run mkdir -p "$FORGE_ROOT" "$DATA_ROOT/ledger" "$DATA_ROOT/runtime" "$DATA_ROOT/memory" "$DATA_ROOT/data" "$LOG_DIR" "$ENV_DIR"

  write_file "$ENV_DIR/forge.env" <<EOF
FORGE_ROOT=${FORGE_ROOT}
FORGE_REPO=${REPO_DIR}
FORGE_HOST=127.0.0.1
FORGE_PORT=${FORGE_PORT}
FORGE_APPS=${ENV_DIR}/apps.json
FORGE_TOKEN=${FORGE_TOKEN}
PUBLIC_URL=${PUBLIC_URL}
EOF
  if [ "$DRY_RUN" != "1" ]; then chmod 600 "$ENV_DIR/forge.env"; fi

  write_file "$ENV_DIR/env" <<EOF
PYTHONUNBUFFERED=1
PUBLIC_URL=${PUBLIC_URL}
EOF
  if [ "$DRY_RUN" != "1" ]; then chmod 600 "$ENV_DIR/env"; fi

  write_file "$ENV_DIR/apps.json" <<EOF
{
  "apps": {
    "${APP_NAME}": {
      "repo": "${REPO_DIR}",
      "container": "${APP_NAME}",
      "image": "cortex-mvp:forge",
      "host_port": ${HOST_PORT},
      "port": ${APP_PORT},
      "public_url": "${PUBLIC_URL}",
      "data_root": "${DATA_ROOT}"
    }
  }
}
EOF
}

install_systemd() {
  write_file /etc/systemd/system/cortex-forge.service <<EOF
[Unit]
Description=Cortex Forge control plane
After=network.target docker.service
Requires=docker.service

[Service]
Type=simple
WorkingDirectory=${REPO_DIR}
EnvironmentFile=${ENV_DIR}/forge.env
ExecStart=/usr/bin/python3 -m cortex_forge.server
Restart=on-failure
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
EOF
  run systemctl daemon-reload
  run systemctl enable --now cortex-forge
}

install_caddy() {
  if [ -z "$DOMAIN" ]; then
    echo "DOMAIN not set; skipping public Caddy route. Forge API remains on 127.0.0.1:${FORGE_PORT}."
    return 0
  fi
  write_file /etc/caddy/Caddyfile <<EOF
${DOMAIN} {
  reverse_proxy 127.0.0.1:${HOST_PORT}
  header {
    X-Content-Type-Options nosniff
    Referrer-Policy no-referrer
  }
}

forge.${DOMAIN} {
  reverse_proxy 127.0.0.1:${FORGE_PORT}
  header {
    X-Content-Type-Options nosniff
    Referrer-Policy no-referrer
  }
}
EOF
  run systemctl reload caddy
}

smoke() {
  echo "Forge dashboard: http://127.0.0.1:${FORGE_PORT}/ui"
  if [ -n "$DOMAIN" ]; then echo "Public Forge dashboard: https://forge.${DOMAIN}/ui"; fi
  if [ "$DRY_RUN" != "1" ]; then
    curl -fsS "http://127.0.0.1:${FORGE_PORT}/forge/status" >/dev/null || {
      echo "warning: forge status smoke failed" >&2
      exit 1
    }
  fi
}

main() {
  need_root
  install_packages
  clone_or_update_repo
  write_config
  install_systemd
  install_caddy
  smoke
  echo "forge bootstrap: pass"
  echo "Next deploy: curl -X POST http://127.0.0.1:${FORGE_PORT}/forge/apps/${APP_NAME}/deploy -H 'authorization: Bearer <token>' -H 'content-type: application/json' -d '{\"witness\":\"human\",\"confirmed\":true}'"
}

main "$@"
