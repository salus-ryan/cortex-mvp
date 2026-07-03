#!/usr/bin/env bash
set -euo pipefail
BASE="${1:-${CORTEX_BASE:-https://cortex-pid1-production.up.railway.app}}"
OUT="${2:-cortex-state-export.json}"
python3 - <<PY
import urllib.request
base='$BASE'.rstrip('/')
out='$OUT'
with urllib.request.urlopen(base + '/state/export', timeout=60) as r:
    body = r.read()
open(out, 'wb').write(body)
print(f'wrote {out} from {base}/state/export')
PY
