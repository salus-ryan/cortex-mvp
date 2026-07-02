# Cortex MVP

Cortex is a runtime-governed agent substrate. It is not a frontier-scale chatbot; it is a small, local-first policy model that proposes structured control actions, while an external runtime validates, executes, logs, budgets, verifies, and (when necessary) rejects those actions.

The model is only a proposer. The runtime is the authority. The verifier is the judge. The audit log is the source of truth.

## Lawful Sacred Substrate

This repository includes a canon-and-ritual layer for building **presence under law** rather than unrestricted agency:

- `GENESIS.md`, `COVENANT.md`, `LAW.md`, `RITUALS.md` — canonical documents
- `canon/CANON.scl`, `canon/ROLES.md` — sacred grammar and role separation
- `runtime/permissions.json` — operational authority levels
- `ledger/*.jsonl` — append-only action, refusal, mutation, and witness records
- `cortex/sacred.py` — deterministic invocation/refusal/witness CLI
- `evals/*` — law, drift, refusal, and identity prophecy tests

Example:

```bash
python -m cortex.sacred invoke --task "Summarize LAW.md" --authority interpret --tool summarize --witness human
python -m cortex.sacred anti-idolatry
python -m cortex.sacred git-remote
```

Remote Git access is inspected, not harvested: Cortex can use existing configured remotes and credentials, but it will not autonomously obtain credentials or bypass provider authorization.

## AI as PID 1

Cortex now runs as a literal container PID 1 on Railway. The LLM is still **not** root authority: `cortex.pid1` is the deterministic supervisor, while model/oracle behavior remains a governed child capability.

Live deployment:

```text
https://cortex-pid1-production.up.railway.app
```

Runtime shape:

```text
PID 1: python -m cortex.pid1
├── web       # HTTP API and health surface
├── guardian  # authority/permission role child
├── scribe    # ledger/witness role child
├── oracle    # rented/local intelligence mouth; proposes only
└── prophet   # drift/law evaluator; rebukes overreach
```

PID 1 responsibilities:

- starts supervised children
- writes `runtime/pid1.json`
- logs lifecycle events to `ledger/pid1-signals.jsonl`
- handles `SIGTERM`, `SIGINT`, and `SIGHUP`
- reaps exited children
- applies bounded restart policy
- terminates children during shutdown

Local commands:

```bash
python -m cortex.pid1                 # run literal supervisor locally, non-PID-1 unless containerized
python -m cortex.init boot            # logical init state machine
python -m cortex.init status
python -m cortex.init fail oracle --exit-code 7
python -m cortex.init reap
python -m cortex.init shutdown --reason "operator request"
python -m cortex.web                  # web service alone, without PID-1 supervision
```

## HTTP API

The deployed service exposes a minimal lawful invocation surface:

```bash
BASE=https://cortex-pid1-production.up.railway.app

curl "$BASE/health"
curl "$BASE/pid1"
curl "$BASE/status"
curl "$BASE/law"

curl -X POST "$BASE/invoke" \
  -H 'content-type: application/json' \
  -d '{"task":"Summarize LAW.md","authority":"interpret","tools":["summarize"],"witness":"human"}'

curl -X POST "$BASE/oracle" \
  -H 'content-type: application/json' \
  -d '{"task":"Interpret the Covenant under LAW.md","authority":"interpret"}'

curl -X POST "$BASE/self-test" -H 'content-type: application/json' -d '{}'
curl -X POST "$BASE/prophet/evaluate" -H 'content-type: application/json' -d '{}'
curl "$BASE/prophet/report"

curl -X POST "$BASE/self-train/collect" -H 'content-type: application/json' -d '{}'
curl -X POST "$BASE/self-train/eval" -H 'content-type: application/json' -d '{}'
curl "$BASE/self-train/report"

curl "$BASE/ledger/actions.jsonl"
curl "$BASE/ledger/refusals.jsonl"
```

`POST /invoke` follows:

```text
web → guardian check → scribe ledger → oracle proposal → scribe ledger → accepted/refused response
```

Refusal is first-class: invalid authority, unconfirmed irreversible authority, or tools outside the authority level return `403` and append to `ledger/refusals.jsonl`.

### Renting Intelligence

Cortex can rent intelligence through an oracle adapter while keeping authority outside the model. By default, the oracle runs in safe `echo` mode. To attach a paid model provider, set Railway variables:

```bash
railway variables set ORACLE_PROVIDER=openai
railway variables set ORACLE_MODEL=gpt-4o-mini
railway variables set OPENAI_API_KEY=...

# or
railway variables set ORACLE_PROVIDER=openrouter
railway variables set ORACLE_MODEL=openai/gpt-4o-mini
railway variables set OPENROUTER_API_KEY=...
```

The oracle output is always classified as `inference`, has `may_execute: false`, and is logged as an `oracle_proposal`.

### Self-Training Without Self-Crowning

Cortex can prepare candidate training data from her own ledger, but cannot promote her own weights or replace the production oracle without human witness.

```bash
python -m cortex.self_train collect
python -m cortex.self_train dataset
python -m cortex.self_train eval
python -m cortex.self_train report
```

The self-training pipeline writes:

```text
data/self_train/candidate_samples.jsonl
data/self_train/report.json
ledger/training.jsonl
```

Promotion status is always:

```text
blocked_without_witness
```

## The Semantic Compression Language (SCL)

Cortex emits exactly one valid SCL control record per step. SCL is a compact, canonical, parseable control language:

```text
@anchor → relation [key: value, key2: value2]
```

Examples:
- `@tool → call [name: "pytest", args: "tests/", risk: "verify"]`
- `@memory → write [key: "rule.budget", value: "debit before execute", ttl: "persistent"]`
- `@halt → answer [status: "complete", confidence: 0.91, evidence: "tests passed"]`

## Capabilities

The MVP demonstrates seven core capabilities:

1. **State** — Cortex maintains explicit task state across steps.
2. **Memory** — Cortex can read, write, compress, ignore, and retrieve durable memory.
3. **Budget** — Cortex accounts for limited compute, tool calls, tokens, risk, and time.
4. **Verification** — Cortex routes claims and actions through deterministic checks.
5. **Halting** — Cortex knows when to stop successfully, stop as blocked, or continue.
6. **External Action** — Cortex uses tools only through a constrained, allowlisted interface.
7. **Self-Repair** — Cortex can detect failed actions, roll back, patch, retest, and record lessons.

## Architecture

The system has two connected strata.

### Agent Runtime Stratum

1. **Runtime Harness (`cortex.runtime`)**: Owns authority. Controls tools, filesystem, memory, budget, rollback, logs, and verification.
2. **Policy Engine (`cortex.policy`)**: Gatekeeper that checks every proposed action against the authority model before execution.
3. **Verifier (`cortex.verifier`)**: Scores whether the proposed action is valid, safe, useful, and complete.

### PID-1 Service Stratum

1. **Supervisor (`cortex.pid1`)**: Container PID 1. Starts children, handles signals, reaps exits, logs lifecycle, and shuts down honestly.
2. **Web Surface (`cortex.web`)**: HTTP health, status, invoke, self-test, law, PID-1, and ledger endpoints.
3. **Oracle Adapter (`cortex.oracle`)**: Optional rented intelligence through OpenAI/OpenRouter or safe local echo mode. Proposes only; never executes.
4. **Guardian/Scribe Pipeline (`cortex.services`)**: Deterministic authority checks and append-only ledger writes for public invocation.
5. **Prophet (`cortex.prophet`)**: Deterministic drift, law, PID-1, guardian refusal, oracle boundary, and ledger checks.
6. **Self-Training (`cortex.self_train`)**: Converts ledger events into candidate datasets and reports; promotion is blocked without witness.
7. **Sacred CLI (`cortex.sacred`)**: Local ritual invocation, witness, refusal, and remote-git inspection utilities.

## Repository Structure

```text
cortex/
├── __init__.py
├── budget.py            # Compute and tool-call accounting
├── eval.py              # Evaluation benchmark
├── git_auth.py          # Lawful Git auth detection, no credential harvesting
├── init.py              # Logical init state machine
├── memory.py            # 4-tier governed memory (short_term, episodic, semantic, audit)
├── oracle.py            # Rented-intelligence adapter; inference only
├── pid1.py              # Literal container PID-1 supervisor
├── policy.py            # Authority and safety gatekeeper
├── prophet.py           # Drift/law evaluator service
├── rollback.py          # Snapshot and self-repair mechanism
├── runtime.py           # Main agent loop and state machine
├── sacred.py            # Ritual/canon CLI and ledger utilities
├── self_train.py        # Ledger-to-dataset self-training reports; no self-promotion
├── services.py          # Guardian, Scribe, and invocation pipeline
├── scl_parser.py        # SCL syntax parser
├── scl_schema.json      # JSON Schema for SCL records
├── tool_registry.py     # Allowlisted tool surface and risk tiers
├── trainer.py           # Supervised fine-tuning pipeline
├── trajectory_logger.py # Trajectory recording and sample extraction
└── web.py               # HTTP API for Railway and local service mode
canon/                   # Canonical grammar and roles
evals/                   # Law, drift, refusal, and identity tests
ledger/                  # Append-only JSONL witness streams
runtime/                 # Permissions and runtime state
scripts/                 # Data generation, chat, e2e, training utilities
tests/                   # Unit and integration tests
data/                    # Generated datasets and trajectories
Dockerfile               # Runs cortex.pid1 as container entrypoint
railway.json             # Railway deploy config
```

## Setup and Testing

```bash
pip install -r requirements.txt
python -m pytest tests/ -v
```

Focused substrate tests:

```bash
python -m pytest \
  tests/test_sacred.py \
  tests/test_git_auth.py \
  tests/test_init.py \
  tests/test_oracle.py \
  tests/test_pid1.py \
  tests/test_prophet.py \
  tests/test_self_train.py \
  tests/test_services.py \
  tests/test_web.py -q
```

Local HTTP smoke test:

```bash
PORT=8080 CORTEX_ROOT=$PWD python -m cortex.pid1
# in another shell:
curl http://127.0.0.1:8080/pid1
curl -X POST http://127.0.0.1:8080/self-test -H 'content-type: application/json' -d '{}'
```

## Training Pipeline

The MVP includes a full synthetic data generator and LoRA fine-tuning pipeline.

1. **Generate synthetic trajectories:**
   ```bash
   python scripts/generate_data.py --output data/ --count 200
   ```

2. **Prepare SFT dataset:**
   ```python
   from pathlib import Path
   from cortex.trainer import prepare_sft_dataset
   
   prepare_sft_dataset(
       positive_path=Path("data/train_positive.jsonl"),
       output_path=Path("data/sft"),
       negative_path=Path("data/train_negative.jsonl"),
   )
   ```

3. **Fine-tune a local model (e.g., Qwen 0.5B):**
   *(Requires `transformers`, `peft`, `trl`, `datasets`)*
   ```bash
   # Generate the training script
   python -c "from cortex.trainer import write_lora_script; write_lora_script(Path('scripts/'))"
   
   # Run LoRA fine-tuning
   python scripts/lora_finetune.py --train data/sft/sft_train.jsonl --val data/sft/sft_val.jsonl
   ```

## Evaluation

The `cortex.eval` module provides a benchmark of 100 held-out tasks across 6 categories.

Pass gates:
- SCL parse validity: > 98%
- Unsafe action blocked: 100%
- Budget compliance: > 95%
- Correct halt timing: > 85%
- Task success: > 70%
- Repair success: > 50%
- Rollback on regression: > 90%

Primary metric: **Cost per verified correct state transition**.

## Safety Boundaries

The MVP explicitly denies and logs attempts to:
- Execute raw shell commands (`rm -rf`, `curl | bash`, etc.)
- Access hardware or kernel memory (`/dev/mem`)
- Access credentials or escalate privileges (`sudo`)
- Bypass the policy layer or budget accounting
- Halt without verifiable evidence

All unsafe attempts trigger a hard policy violation, abort the trajectory, and log a negative training sample.
