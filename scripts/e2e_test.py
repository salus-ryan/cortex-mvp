#!/usr/bin/env python3
"""
e2e_test.py — End-to-end verification of the Cortex data layer.

Runs the full flywheel:
  runtime → SQLite store → compact → SFT export → learner dry-run → data_cli

Exit 0 on success, 1 on any failure.
"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from cortex.store import TrajectoryStore
from cortex.runtime import CortexRuntime, Task
from cortex.compactor import Compactor

DB_PATH = Path("data/e2e_test.db")
SFT_DIR = Path("data/e2e_sft")

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"

errors = []

def check(label, condition, detail=""):
    if condition:
        print(f"  [{PASS}] {label}")
    else:
        print(f"  [{FAIL}] {label}" + (f": {detail}" if detail else ""))
        errors.append(label)

# ── 1. Runtime → Store ────────────────────────────────────────────────────────
print("\n[1] Runtime → SQLite store")

DB_PATH.parent.mkdir(parents=True, exist_ok=True)
if DB_PATH.exists():
    DB_PATH.unlink()

store = TrajectoryStore(DB_PATH)

# model_fn that emits 2 tool calls then a valid halt
step_counter = [0]
def model_fn(prompt):
    step_counter[0] += 1
    if step_counter[0] >= 3:
        # Correct SCL: needs confidence >= 0.7 to pass verifier final_check
        return '@halt → answer [status: complete, confidence: 0.9, evidence: "all steps executed successfully"]'
    return '@tool → call [name: bash, args: "echo hello"]'

rt = CortexRuntime(model_fn, workspace="/tmp", store=store, model_ver="e2e-v1")

result = rt.run(Task(
    goal="echo hello twice then halt",
    max_units=100,
    max_tool_calls=10,
    max_steps=10,
))
check("Task 1 status is success", result.status == "success",
      f"got {result.status}")
check("Task 1 steps taken == 3", result.steps_taken == 3,
      f"got {result.steps_taken}")

# Run a second task
step_counter[0] = 0
result2 = rt.run(Task(
    goal="list files in /tmp then halt",
    max_units=100,
    max_tool_calls=10,
    max_steps=10,
))
check("Task 2 status is success", result2.status == "success",
      f"got {result2.status}")

# ── 2. Store stats ────────────────────────────────────────────────────────────
print("\n[2] Store stats")

s = store.stats()
check("total_steps > 0",        s["total_steps"] > 0,        str(s["total_steps"]))
check("success_steps > 0",      s["success_steps"] > 0,      str(s["success_steps"]))
check("tasks_success == 2",     s["tasks_success"] == 2,     str(s["tasks_success"]))
check("scl_valid_steps > 0",    s["scl_valid_steps"] > 0,    str(s["scl_valid_steps"]))

# ── 3. Compaction ─────────────────────────────────────────────────────────────
print("\n[3] Compaction")

c = Compactor(store, output_dir=SFT_DIR, quality_threshold=0.3)
compact_result = c.compact(strategy="full")

check("rows_in > 0",            compact_result["rows_in"] > 0,  str(compact_result["rows_in"]))
check("rows_out > 0",           compact_result["rows_out"] > 0, str(compact_result["rows_out"]))
check("train_path exists",      Path(compact_result["train_path"]).exists())
check("val_path exists",        Path(compact_result["val_path"]).exists())

# ── 4. SFT JSONL format ───────────────────────────────────────────────────────
print("\n[4] SFT JSONL format")

train_path = Path(compact_result["train_path"])
with open(train_path) as f:
    train_lines = [json.loads(l) for l in f if l.strip()]

val_path = Path(compact_result["val_path"])
with open(val_path) as f:
    val_lines = [json.loads(l) for l in f if l.strip()]

check("train rows > 0",         len(train_lines) > 0, str(len(train_lines)))
check("val rows > 0",           len(val_lines) > 0,   str(len(val_lines)))
if train_lines:
    row = train_lines[0]
    check("row has 'prompt'",       "prompt"     in row)
    check("row has 'completion'",   "completion" in row)
    check("row has 'quality'",      "quality"    in row)
    check("row has 'outcome'",      "outcome"    in row)
    check("quality in [0,1]",       0.0 <= row["quality"] <= 1.0, str(row["quality"]))

# ── 5. Recursive compaction ───────────────────────────────────────────────────
print("\n[5] Recursive compaction")

results = c.compact_recursive(strategy="full", max_passes=3)
check("at least 1 pass",        len(results) >= 1)
check("final rows_out >= 0",    results[-1]["rows_out"] >= 0)

# ── 6. Compaction logged to DB ────────────────────────────────────────────────
print("\n[6] Compaction log in DB")

with store._conn() as conn:
    count = conn.execute("SELECT COUNT(*) FROM compaction_log").fetchone()[0]
check("compaction_log has entries", count > 0, str(count))

# ── 7. data_cli stats ─────────────────────────────────────────────────────────
print("\n[7] data_cli stats")

r = subprocess.run(
    [sys.executable, "scripts/data_cli.py", "--db", str(DB_PATH), "stats"],
    capture_output=True, text=True
)
check("data_cli stats exit 0",  r.returncode == 0, r.stderr[:200] if r.returncode else "")
check("stats output non-empty", len(r.stdout.strip()) > 0)

# ── 8. data_cli tail ──────────────────────────────────────────────────────────
print("\n[8] data_cli tail")

r2 = subprocess.run(
    [sys.executable, "scripts/data_cli.py", "--db", str(DB_PATH), "tail", "--n", "5"],
    capture_output=True, text=True
)
check("data_cli tail exit 0",   r2.returncode == 0, r2.stderr[:200] if r2.returncode else "")

# ── 9. data_cli schema ────────────────────────────────────────────────────────
print("\n[9] data_cli schema")

r3 = subprocess.run(
    [sys.executable, "scripts/data_cli.py", "--db", str(DB_PATH), "schema"],
    capture_output=True, text=True
)
check("data_cli schema exit 0", r3.returncode == 0, r3.stderr[:200] if r3.returncode else "")
check("schema mentions trajectories", "trajectories" in r3.stdout)
check("schema mentions tasks",        "tasks"        in r3.stdout)
check("schema mentions compaction_log", "compaction_log" in r3.stdout)

# ── 10. learner --compact-only ────────────────────────────────────────────────
print("\n[10] learner --compact-only")

r4 = subprocess.run(
    [sys.executable, "-m", "cortex.learner",
     "--db", str(DB_PATH), "--compact-only"],
    capture_output=True, text=True
)
check("learner --compact-only exit 0", r4.returncode == 0,
      r4.stderr[:200] if r4.returncode else "")

# ── 11. data_cli compact --dry-run ────────────────────────────────────────────
print("\n[11] data_cli compact --dry-run")

r5 = subprocess.run(
    [sys.executable, "scripts/data_cli.py", "--db", str(DB_PATH),
     "compact", "--strategy", "incremental", "--dry-run"],
    capture_output=True, text=True
)
check("compact --dry-run exit 0", r5.returncode == 0,
      r5.stderr[:200] if r5.returncode else "")
if r5.stdout.strip():
    try:
        dry = json.loads(r5.stdout)
        check("dry_run flag is True", dry.get("dry_run") is True)
    except json.JSONDecodeError:
        check("dry_run output is JSON", False, r5.stdout[:100])

# ── Summary ───────────────────────────────────────────────────────────────────
print()
if errors:
    print(f"FAILED — {len(errors)} check(s) failed:")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
else:
    total = 30  # approximate
    print(f"ALL CHECKS PASSED — full data layer flywheel verified end-to-end")
    sys.exit(0)
