# Cortex MVP

Cortex is a runtime-governed agent substrate. It is not a frontier-scale chatbot; it is a small, local-first policy model that proposes structured control actions, while an external runtime validates, executes, logs, budgets, verifies, and (when necessary) rejects those actions.

The model is only a proposer. The runtime is the authority. The verifier is the judge. The audit log is the source of truth.

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

The system consists of three layers:

1. **Runtime Harness (`cortex.runtime`)**: Owns authority. Controls tools, filesystem, memory, budget, rollback, logs, and verification.
2. **Policy Engine (`cortex.policy`)**: Gatekeeper that checks every proposed action against the authority model before execution.
3. **Verifier (`cortex.verifier`)**: Scores whether the proposed action is valid, safe, useful, and complete.

## Repository Structure

```text
cortex/
├── __init__.py
├── budget.py            # Compute and tool-call accounting
├── eval.py              # Evaluation benchmark (100 held-out tasks)
├── memory.py            # 4-tier governed memory (short_term, episodic, semantic, audit)
├── policy.py            # Authority and safety gatekeeper
├── rollback.py          # Snapshot and self-repair mechanism
├── runtime.py           # Main agent loop and state machine
├── scl_parser.py        # SCL syntax parser
├── scl_schema.json      # JSON Schema for SCL records
├── tool_registry.py     # Allowlisted tool surface and risk tiers
├── trainer.py           # Supervised fine-tuning pipeline
└── trajectory_logger.py # Trajectory recording and sample extraction
scripts/
└── generate_data.py     # Synthetic data generator for 13 task families
tests/
└── ...                  # 118 unit and integration tests
data/
└── ...                  # Generated datasets and trajectories
```

## Setup and Testing

```bash
pip install -r requirements.txt
python -m pytest tests/ -v
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
