# Cortex Quickstart

Cortex is a governed agent runtime: models propose actions, while the runtime validates, budgets, logs, verifies, and can refuse or roll back actions.

This guide gets a new developer to a local, testable Cortex in about five minutes.

## 1. Install

```bash
git clone https://github.com/salus-ryan/cortex-mvp.git
cd cortex-mvp
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

If you only want the runtime/tests, you may skip the optional training packages listed in `requirements.txt` after the core/test section.

## 2. Verify the repo

```bash
python -m pytest -q
```

Expected: all tests pass.

## 3. Start the local web service

```bash
python -m cortex.web
```

Then check:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/foundry/repos
curl http://127.0.0.1:8000/oauth/status
```

## 4. Try the OpenAI-compatible endpoint

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H 'content-type: application/json' \
  -d '{"model":"cortex-local-mind-v1","messages":[{"role":"user","content":"Explain @tool in one sentence."}]}'
```

Cortex returns a proposal/response, not unrestricted execution.

## 5. Use explicit file mentions

Cortex can include bounded, read-only workspace files when a prompt names them with `@{path}`:

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H 'content-type: application/json' \
  -d '{"model":"cortex-local-mind-v1","messages":[{"role":"user","content":"Summarize @{LAW.md}"}]}'
```

The resolver refuses paths outside the workspace and caps file count/bytes.

## 6. OAuth/mobile path

The mobile PWA uses `/oauth/status` and `/oauth/login` when OIDC is configured. Minimum variables are:

```text
CORTEX_OIDC_CLIENT_ID
CORTEX_OIDC_REDIRECT_URI
CORTEX_OIDC_AUTHORIZATION_ENDPOINT
CORTEX_OIDC_TOKEN_ENDPOINT
```

Confidential clients may also set `CORTEX_OIDC_CLIENT_SECRET`. Cortex reports whether it is configured without returning the value.

## 7. Audit posture

Runtime actions can be written to a tamper-evident audit chain:

```python
from cortex.audit_sink import AuditSink
print(AuditSink('ledger/audit.jsonl').verify())
```

See `docs/soc2/audit_sink.md` and `docs/soc2/README.md`.

## What Cortex is good for today

- Safe developer-agent experiments.
- Auditable tool/action governance.
- Local/mobile demos with explicit auth.
- Teams evaluating runtime controls before letting agents mutate repos.

## What it is not yet

- A formal SOC 2 attestation.
- A fully hardened production sandbox.
- A general chatbot with arbitrary web/tool access.
