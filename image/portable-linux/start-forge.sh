#!/usr/bin/env bash
set -euo pipefail

# Start Forge dashboard from a portable USB layout.

TARGET="${1:-}"
DRY_RUN="${DRY_RUN:-0}"

if [ -z "$TARGET" ]; then
  echo "usage: $0 /path/to/mounted-usb" >&2
  exit 2
fi

REPO="$TARGET/cortex-mvp"
ENV_FILE="$TARGET/env/forge.env"

run() {
  echo "+ $*"
  if [ "$DRY_RUN" != "1" ]; then "$@"; fi
}

if [ ! -f "$ENV_FILE" ] && [ "$DRY_RUN" != "1" ]; then
  echo "refused: missing $ENV_FILE; run install.sh first" >&2
  exit 3
fi

set -a
if [ "$DRY_RUN" != "1" ]; then . "$ENV_FILE"; fi
set +a

cd "$REPO"
run python3 -m cortex_forge.server
