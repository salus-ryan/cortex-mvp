#!/usr/bin/env bash
set -euo pipefail

# Install Cortex portable layout onto a mounted USB directory.
# Usage: sudo image/portable-linux/install.sh /media/$USER/CORTEX

TARGET="${1:-}"
DRY_RUN="${DRY_RUN:-0}"
REPO_URL="${REPO_URL:-https://github.com/salus-ryan/cortex-mvp.git}"
APP_PORT="${APP_PORT:-8080}"
FORGE_PORT="${FORGE_PORT:-8765}"
PUBLIC_URL="${PUBLIC_URL:-http://127.0.0.1:${APP_PORT}}"

if [ -z "$TARGET" ]; then
  echo "usage: $0 /path/to/mounted-usb" >&2
  exit 2
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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

run mkdir -p "$TARGET"
run mkdir -p "$TARGET/state/ledger" "$TARGET/state/memory" "$TARGET/state/runtime" "$TARGET/state/data" "$TARGET/env"

if [ -d "$TARGET/cortex-mvp/.git" ]; then
  echo "portable repo already exists at $TARGET/cortex-mvp"
elif [ "$DRY_RUN" != "1" ]; then
  if command -v rsync >/dev/null 2>&1; then
    run rsync -a --delete --exclude .git --exclude .venv --exclude __pycache__ "$REPO_ROOT/" "$TARGET/cortex-mvp/"
  else
    run mkdir -p "$TARGET/cortex-mvp"
    (cd "$REPO_ROOT" && tar --exclude .git --exclude .venv --exclude __pycache__ -cf - .) | (cd "$TARGET/cortex-mvp" && tar -xf -)
  fi
else
  echo "+ copy repo to $TARGET/cortex-mvp"
fi

write_file "$TARGET/env/cortex.env" <<EOF
PYTHONUNBUFFERED=1
PUBLIC_URL=${PUBLIC_URL}
CORTEX_LEDGER=/cortex-state/ledger
CORTEX_MEMORY=/cortex-state/memory
CORTEX_RUNTIME=/cortex-state/runtime
CORTEX_DATA=/cortex-state/data
EOF

write_file "$TARGET/env/forge.env" <<EOF
FORGE_ROOT=${TARGET}/state/forge
FORGE_REPO=${TARGET}/cortex-mvp
FORGE_HOST=127.0.0.1
FORGE_PORT=${FORGE_PORT}
FORGE_APPS=${TARGET}/env/apps.json
PUBLIC_URL=${PUBLIC_URL}
EOF

write_file "$TARGET/env/apps.json" <<EOF
{
  "apps": {
    "cortex": {
      "repo": "${TARGET}/cortex-mvp",
      "container": "cortex-usb",
      "image": "cortex-mvp:usb",
      "host_port": ${APP_PORT},
      "port": 8080,
      "public_url": "${PUBLIC_URL}",
      "data_root": "${TARGET}/state"
    }
  }
}
EOF

run chmod +x "$TARGET/cortex-mvp/image/portable-linux/start.sh" "$TARGET/cortex-mvp/image/portable-linux/start-forge.sh" "$TARGET/cortex-mvp/image/portable-linux/status.sh"

echo "portable Cortex USB layout ready at $TARGET"
echo "start Cortex: $TARGET/cortex-mvp/image/portable-linux/start.sh $TARGET"
echo "start Forge:  $TARGET/cortex-mvp/image/portable-linux/start-forge.sh $TARGET"
