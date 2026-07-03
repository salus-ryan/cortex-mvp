# Cortex Persistence and Private State Vault

Railway container filesystems may be ephemeral unless a volume is configured. Cortex therefore exposes explicit state export/import as a minimal private vault pattern inspired by `elevate-foundry/black-box`.

## Endpoints

```http
GET  /state/manifest
GET  /state/export
POST /state/import
```

## Export

```bash
scripts/export_state.sh https://cortex-pid1-production.up.railway.app cortex-state-export.json
```

Exports readable state from:

```text
memory/*.jsonl
ledger/*.jsonl
```

Excludes known secret/sensitive streams:

```text
ledger/auth.jsonl
ledger/payments.jsonl
runtime/auth_failures.json
```

## Import

Import is narrow: only `memory/*.jsonl` files are restored. It requires witness + confirmation + auth.

```bash
railway run -- bash -lc 'CORTEX_AUTH_TOKEN="$CORTEX_AUTH_TOKEN" WITNESS=Ryan CONFIRMED=true scripts/import_state.sh https://cortex-pid1-production.up.railway.app cortex-state-export.json'
```

## Trust posture

```text
local-first
explicit export
witnessed import
secrets excluded
forget tombstones preserved
human-owned backup files
```

This is not a substitute for a real Railway volume, but it prevents memory from being trapped in an ephemeral runtime.
