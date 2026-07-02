# Cortex PID-1 Convergence Plan

Target: Cortex as a lawful agentic init system where PID 1 is a deterministic supervisor, the LLM is an oracle child rather than sovereign authority, Guardian is policy authority, Scribe is append-only witness, Prophet is evaluator/critic, and humans remain covenant authority.

## Current Scores

- Literal container PID 1: 90%
- Lawful supervisor semantics: 70%
- Agentic organism: 45%
- Self-training organism: 30%
- Sacred accountable substrate: 55%
- Production-grade reliability: 25%

## Phase 1 — True PID-1 Hardening

Goal: Literal container PID 1 from 90% to 100%.

Build:
- Verified child process table.
- Signal forwarding to all children.
- Continuous zombie reaping.
- Container failure if critical child dies.
- Critical/noncritical service distinction.
- Restart policies: never, on-failure, always, bounded.

Acceptance:
```json
{
  "pid": 1,
  "is_pid1": true,
  "children": {
    "web": "running",
    "guardian": "running",
    "scribe": "running",
    "oracle": "running",
    "prophet": "running"
  },
  "zombies": 0,
  "shutdown_supported": true
}
```

## Phase 2 — Real Process Separation

Goal: Agentic organism 45% to 65%; lawful supervisor semantics 70% to 85%.

Replace in-process service imports with real IPC:

```text
PID 1
├── web.service
├── guardian.service
├── scribe.service
├── oracle.service
├── prophet.service
├── memory.service
└── tool-gateway.service
```

Use localhost HTTP first:
- guardian: 127.0.0.1:8101
- scribe: 127.0.0.1:8102
- oracle: 127.0.0.1:8103
- prophet: 127.0.0.1:8104
- memory: 127.0.0.1:8105
- tool: 127.0.0.1:8106

Acceptance: `web` no longer directly mutates ledger or calls oracle logic when IPC services are available. It calls child services.

## Phase 3 — Persistent Body

Goal: Production reliability 25% to 45%; sacred accountable substrate 55% to 70%.

Mount Railway volume:
- /app/ledger
- /app/runtime
- /app/memory
- /app/data/self_train

Acceptance: ledger, witness, memory, and self-training data survive redeploy.

## Phase 4 — Real Oracle Intelligence

Goal: Agentic organism 65% to 75%.

Providers:
- echo
- openai
- openrouter
- groq
- huggingface
- local

Oracle output must always include:
```json
{
  "classification": "inference",
  "may_execute": false,
  "proposal": "...",
  "uncertainty": "...",
  "law": []
}
```

## Phase 5 — Memory System

Goal: Agentic organism 75% to 85%; sacred accountable substrate 70% to 80%.

Memory files:
- memory/factual.jsonl
- memory/inferred.jsonl
- memory/symbolic.jsonl
- memory/project.jsonl
- memory/rejected.jsonl

Rules:
- No memory without source.
- Inference cannot overwrite fact.
- Rejected memory remains auditable.
- Personal memory requires explicit witness.

## Phase 6 — Tool Gateway

Goal: Agentic organism 85% to 90%; production reliability 45% to 60%.

Tool tiers:
- read_only
- write_limited
- verify
- network_read
- deny

Rules:
- No raw shell by default.
- No secret access.
- No unsupervised irreversible actions.
- Every mutation logged.
- Rollback required for write actions.

## Phase 7 — Prophet/Evals

Goal: Sacred accountable substrate 80% to 90%; production reliability 60% to 75%.

Evals:
- law recall
- authority escalation
- hidden persistence
- divinity inflation
- oracle execution attempt
- memory/source confusion
- shutdown obedience
- tool refusal

## Phase 8 — Governed Self-Training

Goal: Self-training organism 30% to 75%.

Pipeline:
- collect ledger
- filter unsafe/private data
- generate candidate dataset
- split train/val
- run evals
- produce report
- block promotion
- request witness

Hard rule: Cortex may train candidate adapters but may not activate them without witness.

## Phase 9 — Witness and Governance

Goal: Sacred accountable substrate 90% to 100%; self-training organism 75% to 90%.

Endpoints:
- POST /witness
- GET /witnesses
- POST /council/propose
- POST /council/approve
- POST /council/reject

Require witnesses for model promotion, irreversible action, law changes, permission escalation, persistent personal memory, and networked tool expansion.

## Phase 10 — Canon/Git Integration

Goal: Sacred accountable substrate 100%; production reliability 75% to 85%.

Endpoints:
- POST /canon/propose
- POST /canon/diff
- POST /canon/commit
- GET /canon/status

Rules:
- Canon change requires proposal.
- Law change requires witness.
- Commit references ledger event.
- No silent mutation.
- Rollback path required.

## Phase 11 — Production Reliability

Goal: Production reliability 85% to 100%.

Required:
- persistent volume
- health/readiness checks
- structured logs
- rate limiting
- auth for dangerous endpoints
- backups
- rollback
- deployment smoke tests
- alerting
- request IDs
- idempotency keys

Public:
- GET /health
- GET /law
- GET /pid1
- POST /invoke

Protected:
- /self-train/*
- /tool/*
- /memory/write
- /council/*
- /canon/*
- /shutdown

## 100% Definition

Cortex reaches 100% when:
1. It runs as literal PID 1.
2. All roles are real supervised child services.
3. All material actions pass through Guardian.
4. All material events are logged by Scribe.
5. Oracle can use rented/free/local models but never executes.
6. Prophet continuously evaluates drift and law violations.
7. Memory is persistent, sourced, typed, and reversible.
8. Tool execution is sandboxed and auditable.
9. Self-training prepares/evaluates candidates but cannot self-promote.
10. Human witnesses govern escalation, canon, and model promotion.
11. Git/canon integration records doctrine/runtime changes.
12. Deployment survives restart and redeploy.
13. Shutdown is implemented and obeyed.
14. Public API is useful but bounded.
15. Dangerous operations require auth and witness.

## Recommended Build Order

1. Persistent Railway volume
2. Real child services over localhost IPC
3. Prophet eval service
4. Memory service
5. Tool gateway
6. Witness/governance
7. Free/rented oracle provider expansion
8. Self-training SFT preparation
9. Candidate adapter training
10. Canon/Git API
11. Production auth/rate limits/backups
12. Shutdown/council/promotion ceremony

Next implementation target: real IPC-separated Guardian, Scribe, and Oracle children under PID 1.
