#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-/opt/cortex}"

require() {
  if [ ! -e "$ROOT/$1" ]; then
    echo "missing: $ROOT/$1" >&2
    exit 1
  fi
}

require cortex/pid1.py
require cortex/runtime.py
require cortex/scl_parser.py
require cortex/scl_emitter.py
require cortex/scl_spec.py
require runtime/permissions.json
require LAW.md
require image/live-usb/cortex-init
require image/live-usb/mount-cortex-state

if command -v python3 >/dev/null 2>&1; then
  (cd "$ROOT" && python3 - <<'PY'
from cortex.pid1 import child_specs_for_profile
from cortex.scl_parser import parse
assert child_specs_for_profile('compact')
assert parse('@halt → answer [status: "complete", confidence: 1.0, evidence: "layout verified"]').valid
print('cortex live layout verified')
PY
  )
fi
