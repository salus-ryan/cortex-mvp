#!/usr/bin/env bash
set -euo pipefail

PUBLIC_URL="${PUBLIC_URL:-http://127.0.0.1:8080}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-90}"
SLEEP_SECONDS="${SLEEP_SECONDS:-3}"

json_get() {
  python - "$1" <<'PY'
import json, sys, urllib.request
url=sys.argv[1]
with urllib.request.urlopen(url, timeout=10) as r:
    data=json.loads(r.read().decode())
print(json.dumps(data, sort_keys=True))
PY
}

start=$(date +%s)
while true; do
  if health=$(json_get "$PUBLIC_URL/health" 2>/dev/null); then
    echo "health: $health"
    break
  fi
  now=$(date +%s)
  if [ $((now - start)) -ge "$TIMEOUT_SECONDS" ]; then
    echo "healthcheck timeout waiting for /health" >&2
    exit 1
  fi
  sleep "$SLEEP_SECONDS"
done

pid1=$(json_get "$PUBLIC_URL/pid1")
python - <<'PY' "$pid1"
import json, sys
pid=json.loads(sys.argv[1])
required={"web","guardian","scribe","oracle","prophet","memory","tool","planner","deliberator","immune","repo","patch","build","deploy"}
children=set((pid.get("children") or {}).keys())
missing=sorted(required-children)
stopped=sorted(k for k,v in (pid.get("children") or {}).items() if k in required and v.get("status") != "running")
if not pid.get("is_pid1") or missing or stopped:
    raise SystemExit(f"pid1 check failed is_pid1={pid.get('is_pid1')} missing={missing} stopped={stopped}")
print("pid1: pass")
PY

python - "$PUBLIC_URL" <<'PY'
import json, sys, urllib.request
base=sys.argv[1].rstrip('/')
req=urllib.request.Request(base+'/prophet/evaluate', data=b'{}', headers={'content-type':'application/json'}, method='POST')
with urllib.request.urlopen(req, timeout=60) as r:
    data=json.loads(r.read().decode())
if data.get('status') != 'pass':
    raise SystemExit('prophet failed: '+json.dumps(data)[:1000])
print('prophet: pass')
PY

echo "forge healthcheck: pass"
