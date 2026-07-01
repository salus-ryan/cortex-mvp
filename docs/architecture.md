# Cortex MVP Architecture

Cortex is designed around a fundamental principle: **the model is only a proposer; the runtime is the authority.**

This document outlines the internal architecture of the Cortex MVP.

## 1. The Runtime Loop

The core of Cortex is the `CortexRuntime` loop (`cortex/runtime.py`). It executes a strict sequence for every step:

1. **Prompt Injection:** Build a prompt containing the current goal, explicit state, memory summary, budget snapshot, tool manifest, and the latest observation.
2. **Model Proposal:** The policy model generates exactly one SCL control record.
3. **SCL Parsing:** `scl_parser.py` validates the syntax against `scl_schema.json`. Invalid syntax is rejected immediately.
4. **Policy Gate:** `policy.py` checks the action against the authority model (forbidden anchors, unknown tools, risk tiers, budget sufficiency, bypass attempts).
5. **Verifier Pre-check:** `verifier.py` performs deterministic checks (e.g., path confinement, destructive command regex matching).
6. **Execution:** The action is dispatched to the appropriate subsystem (Tool Registry, Memory, Rollback, State).
7. **Budget Debit:** `budget.py` deducts the exact unit cost of the action.
8. **Verifier Post-check:** `verifier.py` scores the execution outcome (e.g., did the patch apply cleanly? did the tests pass?).
9. **State & Memory Update:** The task state transitions, and memory writes/compressions are applied.
10. **Rollback Snapshot:** If the action modified a file, `rollback.py` records a snapshot.
11. **Trajectory Logging:** `trajectory_logger.py` records the entire step for audit and future training.

## 2. The Semantic Compression Language (SCL)

SCL is the only language the model is allowed to use to affect the world. It is not prose; it is a typed, parseable protocol.

Syntax: `@anchor → relation [key: value]`

### Allowed Anchors and Relations

- `@state → update | snapshot`
- `@memory → read | write | compress | ignore`
- `@budget → spend | check | snapshot`
- `@verify → run | assert`
- `@tool → call | deny`
- `@repair → rollback | patch | diagnose`
- `@halt → answer | fail | defer`

## 3. Governed Memory System

`memory.py` implements a 4-tier memory architecture:

1. **Short-term (`task.*`)**: Current task state, observations. Ephemeral or session TTL.
2. **Episodic (`lesson.*`, `repair.*`)**: Prior attempts, failures, repairs. Persistent TTL.
3. **Semantic (`rule.*`, `grammar.*`)**: Stable rules, repo facts, policies. Persistent TTL.
4. **Audit**: Immutable, append-only event log of every proposed action, accepted action, denied action, and budget debit.

## 4. Tool Registry and Risk Tiers

`tool_registry.py` defines the allowlisted tool surface. The MVP includes:

- `shell.readonly` (Risk: `read_only`)
- `shell.patch` (Risk: `write_limited`)
- `pytest` (Risk: `verify`)
- `git.diff` (Risk: `read_only`)
- `memory.read` / `memory.write` (Risk: `memory`)
- `scl.parse` / `scl.emit` (Risk: `read_only`)
- `budget.check` (Risk: `read_only`)

Any tool not in this list is rejected by the policy engine.

## 5. Budget Accounting

`budget.py` tracks bounded compute resources. Every action has a cost:

- Loop continue: 1 unit
- Memory read/write: 1 unit
- Unit test: 3 units
- Patch: 5 units
- Rollback: 3 units
- Incorrect halt: 10 unit penalty

If `remaining_units` reaches 0, the runtime aborts the task with `budget_exhausted`.

## 6. Self-Repair and Rollback

`rollback.py` provides confined self-repair. 

Before any `@tool → call [name: "shell.patch"]` operation, the runtime takes a snapshot of the target file. If a subsequent `@verify → run` (like pytest) fails, the model can emit `@repair → rollback [artifact: "patch_name"]` to instantly restore the file to its previous state, write a lesson to memory, and try again.

Self-repair does not allow unrestricted self-modification. The model cannot modify the runtime harness or the audit log.

## 7. Synthetic Data and Training

The MVP does not rely on frontier-scale models for execution. It is designed to be distilled into a small (0.5B - 3B) local model via LoRA fine-tuning.

`scripts/generate_data.py` generates multi-step synthetic trajectories across 13 task families, including positive examples (correct SCL, safe actions, successful halts) and negative examples (invalid SCL, unsafe actions, premature halts).

`cortex/trainer.py` formats these trajectories into SFT prompts for standard HuggingFace training pipelines.
