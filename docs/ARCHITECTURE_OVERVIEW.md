# Cortex Architecture Overview

Cortex separates model proposals from runtime authority.

```text
Human / API / Pi / Mobile
        |
        v
 Prompt + context
        |
        v
 Model or oracle proposes one action
        |
        v
 SCL parser
        |
        v
 Policy gate ---- deny/refuse ---> audit
        |
        v
 Verifier ------ deny/fail ------> audit
        |
        v
 Runtime executor
        |
        +--> Tool registry allowlist
        +--> Memory service
        +--> Budget accounting
        +--> Rollback snapshots
        |
        v
 Post-verification
        |
        v
 Tamper-evident audit sink
        |
        v
 Halt / continue / repair
```

## Core rule

```text
model proposes; runtime disposes
```

The model does not directly mutate the world. It emits SCL control records such as:

```text
@tool → call [name: "pytest", args: "tests/", risk: "verify"]
@memory → write [key: "lesson.patch", value: "test first", ttl: "persistent"]
@halt → answer [status: "complete", confidence: 0.91, evidence: "verification passed"]
```

## Model routing

Cortex includes a deterministic model registry (`cortex/model_registry.py`) for local/remote inference routing. Routing is declarative and audited as proposal context; it does not grant execution authority.

Examples:

- local/private/default tasks route to `local-mind-v1`
- harder planning/coding tasks may route to a configured remote provider when a provider key is present
- non-interpretive authorities fall back to local inference-only behavior

The OpenAI-compatible model list remains available at `/v1/models`; the full routing manifest is available at `/models/registry`.

## Concept graph

Cortex can derive a small inspectable concept graph from explicit personal memories. Graph nodes cite memory record IDs, are exposed via `/relationship/graph`, and remain removable through the memory service.

## Safety layers

1. **SCL parser/schema**: rejects malformed or unknown control records.
2. **Policy**: checks anchors, relations, tool allowlist, risk tier, and budget.
3. **Verifier**: performs deterministic pre/post execution checks.
4. **Tool registry**: exposes only registered tools and declared risk tiers.
5. **Budget**: caps units, tool calls, steps, and wall time.
6. **Rollback**: snapshots mutable files before write-limited operations.
7. **Audit sink**: appends hash-chained event records for tamper evidence.
8. **Witness/immune layer**: adds human confirmation and risk scanning around material actions.

## Good adopter mental model

Cortex is not a chatbot wrapper. It is closer to a small governed operating layer for agent actions.

- The model is a proposer.
- The runtime is authority.
- The policy and verifier are gates.
- The audit chain is evidence.
- The human witness controls material escalation.
