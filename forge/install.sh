#!/usr/bin/env bash
set -euo pipefail

DATA_ROOT="${DATA_ROOT:-/var/lib/cortex}"
LOG_DIR="${LOG_DIR:-/var/log/cortex-forge}"
ENV_DIR="${ENV_DIR:-/etc/cortex}"
USER_NAME="${SUDO_USER:-${USER:-root}}"

mkdir -p \
  "$DATA_ROOT/ledger" \
  "$DATA_ROOT/runtime" \
  "$DATA_ROOT/memory" \
  "$DATA_ROOT/data" \
  "$LOG_DIR" \
  "$ENV_DIR"

if [ ! -f "$ENV_DIR/env" ]; then
  cat > "$ENV_DIR/env" <<'EOF'
# Cortex Forge environment
# Add secrets manually. Do not commit this file.
PYTHONUNBUFFERED=1
EOF
  chmod 600 "$ENV_DIR/env"
fi

chown -R "$USER_NAME":"$USER_NAME" "$DATA_ROOT" "$LOG_DIR" 2>/dev/null || true

echo "Forge directories ready:"
echo "  data: $DATA_ROOT"
echo "  logs: $LOG_DIR"
echo "  env : $ENV_DIR/env"

echo "Required host packages: docker caddy(optional) git"
