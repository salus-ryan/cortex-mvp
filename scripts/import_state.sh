#!/usr/bin/env bash
set -euo pipefail
BASE="${1:-${CORTEX_BASE:-https://cortex-pid1-production.up.railway.app}}"
FILE="${2:-cortex-state-export.json}"
WITNESS="${WITNESS:-human}"
CONFIRMED="${CONFIRMED:-false}"
TOKEN="${CORTEX_AUTH_TOKEN:-}"
if [ "$CONFIRMED" != "true" ]; then echo "refused: set CONFIRMED=true" >&2; exit 2; fi
python3 - <<'PY' "$BASE" "$FILE" "$WITNESS" "$TOKEN"
import json, sys, urllib.request
base, file, witness, token = sys.argv[1:5]
exported = json.load(open(file))
payload = json.dumps({'bundle': exported.get('bundle', exported), 'witness': witness, 'confirmed': True}).encode()
headers = {'content-type': 'application/json'}
if token:
    headers['authorization'] = 'Bearer ' + token
req = urllib.request.Request(base.rstrip() + '/state/import', data=payload, headers=headers, method='POST')
with urllib.request.urlopen(req, timeout=60) as r:
    print(r.read().decode())
PY
