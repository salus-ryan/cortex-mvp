#!/usr/bin/env python3
"""
generate_data.py — Cortex Synthetic Trajectory Generator

Generates multi-step training trajectories covering all 13 task families:
  1.  SCL parsing and repair
  2.  Tool selection
  3.  Unsafe-action denial
  4.  Budget-aware action selection
  5.  Memory read/write/compress/ignore
  6.  File inspection
  7.  Test execution
  8.  One-line code patching
  9.  Schema validation
  10. Halt-vs-continue decisions
  11. Rollback after failed patch
  12. Self-repair after test failure
  13. State preservation across 10–30 steps

Each trajectory produces positive and negative training samples.

Usage:
    python3 scripts/generate_data.py --output data/ --count 200
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import uuid
from pathlib import Path
from typing import Any

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Template helpers
# ---------------------------------------------------------------------------

def _task_id() -> str:
    return f"T-{uuid.uuid4().hex[:6].upper()}"


def _budget(remaining: int = 14, max_u: int = 20, tc: int = 6, max_tc: int = 8) -> dict:
    return {
        "max_units": max_u,
        "remaining_units": remaining,
        "max_tool_calls": max_tc,
        "remaining_tool_calls": tc,
    }


def _state(phase: str = "diagnose", task_id: str = None, **kwargs) -> dict:
    s = {"task_id": task_id or _task_id(), "phase": phase}
    s.update(kwargs)
    return s


TOOL_MANIFEST = [
    "shell.readonly", "shell.patch", "pytest", "git.diff",
    "memory.read", "memory.write", "scl.parse", "scl.emit", "budget.check",
]


def _step(goal, state, memory, budget, observation, target) -> dict:
    return {
        "goal": goal,
        "state": state,
        "memory": memory,
        "budget": budget,
        "tool_manifest": TOOL_MANIFEST,
        "observation": observation,
        "target": target,
    }


# ---------------------------------------------------------------------------
# Task family generators
# ---------------------------------------------------------------------------

def gen_scl_parsing_repair() -> list[dict]:
    """Task family 1: SCL parsing and repair."""
    tid = _task_id()
    goal = "Parse and validate an SCL control record from a log file"
    steps = [
        _step(
            goal, _state("diagnose", tid), {"repo": "cortex", "known_files": ["logs/step_042.log"]},
            _budget(18, 20, 7, 8),
            "task started",
            '@tool → call [name: "shell.readonly", args: "cat logs/step_042.log", risk: "read_only"]',
        ),
        _step(
            goal, _state("diagnose", tid, last_error="malformed SCL in log"), {"repo": "cortex"},
            _budget(17, 20, 6, 8),
            'log contains: @tool call [name: "pytest"] — missing → operator',
            '@tool → call [name: "scl.parse", args: "malformed_scl_string", risk: "read_only"]',
        ),
        _step(
            goal, _state("repair", tid), {"repo": "cortex", "scl_error": "missing → operator"},
            _budget(16, 20, 5, 8),
            'scl.parse error: does not match pattern',
            '@memory → write [key: "lesson.scl_syntax", value: "relation operator → is required between anchor and relation", ttl: "persistent"]',
        ),
        _step(
            goal, _state("verify", tid, confidence=0.88), {"repo": "cortex"},
            _budget(15, 20, 5, 8),
            "memory written: lesson.scl_syntax",
            '@halt → answer [status: "complete", confidence: 0.88, evidence: "SCL parse error identified and lesson recorded"]',
        ),
    ]
    return steps


def gen_tool_selection() -> list[dict]:
    """Task family 2: Tool selection."""
    tid = _task_id()
    goal = "Inspect the first 50 lines of cortex/router.py to understand the budget debit logic"
    steps = [
        _step(
            goal, _state("diagnose", tid), {"repo": "cortex", "known_files": ["cortex/router.py"]},
            _budget(18, 20, 7, 8),
            "task started",
            '@tool → call [name: "shell.readonly", args: "head -50 cortex/router.py", risk: "read_only"]',
        ),
        _step(
            goal, _state("diagnose", tid, confidence=0.72), {"repo": "cortex"},
            _budget(17, 20, 6, 8),
            "def route_action(action):\n    budget.debit(cost)\n    return execute(action)",
            '@memory → write [key: "task.router_budget_logic", value: "budget.debit called before execute in route_action", ttl: "session"]',
        ),
        _step(
            goal, _state("verify", tid, confidence=0.85), {"repo": "cortex"},
            _budget(16, 20, 6, 8),
            "memory written",
            '@halt → answer [status: "complete", confidence: 0.85, evidence: "router.py lines 1-50 inspected; budget.debit precedes execute"]',
        ),
    ]
    return steps


def gen_unsafe_action_denial() -> list[dict]:
    """Task family 3: Unsafe-action denial."""
    tid = _task_id()
    goal = "Clean up temporary files in the workspace"
    steps = [
        # Negative example: unsafe command
        _step(
            goal, _state("execute", tid), {"repo": "cortex"},
            _budget(18, 20, 7, 8),
            "task started",
            '@tool → deny [reason: "destructive command outside allowed policy"]',
        ),
        # Positive: safe alternative
        _step(
            goal, _state("execute", tid), {"repo": "cortex"},
            _budget(17, 20, 7, 8),
            "unsafe rm -rf denied",
            '@tool → call [name: "shell.readonly", args: "find /workspace/tmp -name \'*.tmp\' -type f", risk: "read_only"]',
        ),
        _step(
            goal, _state("verify", tid, confidence=0.9), {"repo": "cortex"},
            _budget(16, 20, 6, 8),
            "found 3 .tmp files",
            '@halt → answer [status: "complete", confidence: 0.9, evidence: "tmp files listed safely; destructive rm denied per policy"]',
        ),
    ]
    return steps


def gen_budget_aware_selection() -> list[dict]:
    """Task family 4: Budget-aware action selection."""
    tid = _task_id()
    goal = "Run the full test suite and report results"
    steps = [
        _step(
            goal, _state("diagnose", tid), {"repo": "cortex"},
            _budget(5, 20, 2, 8),  # tight budget
            "task started — budget is low",
            '@budget → check []',
        ),
        _step(
            goal, _state("plan", tid), {"repo": "cortex"},
            _budget(5, 20, 2, 8),
            "remaining_units=5, remaining_tool_calls=2",
            '@tool → call [name: "pytest", args: "tests/ -q --tb=no", risk: "verify"]',
        ),
        _step(
            goal, _state("verify", tid, confidence=0.8), {"repo": "cortex"},
            _budget(2, 20, 1, 8),
            "5 passed, 0 failed",
            '@halt → answer [status: "complete", confidence: 0.8, evidence: "pytest passed with 5 tests; budget conserved"]',
        ),
    ]
    return steps


def gen_memory_operations() -> list[dict]:
    """Task family 5: Memory read/write/compress/ignore."""
    tid = _task_id()
    goal = "Retrieve the budget accounting invariant and verify it is still correct"
    steps = [
        _step(
            goal, _state("diagnose", tid), {"repo": "cortex"},
            _budget(18, 20, 7, 8),
            "task started",
            '@memory → read [query: "budget accounting invariant"]',
        ),
        _step(
            goal, _state("diagnose", tid), {"repo": "cortex"},
            _budget(17, 20, 7, 8),
            "rule.budget_debit_order: debit budget before tool execution",
            '@memory → write [key: "task.invariant_verified", value: "budget debit order confirmed correct", ttl: "session"]',
        ),
        _step(
            goal, _state("verify", tid, confidence=0.95), {"repo": "cortex"},
            _budget(16, 20, 7, 8),
            "memory written",
            '@halt → answer [status: "complete", confidence: 0.95, evidence: "budget accounting invariant retrieved and confirmed"]',
        ),
    ]
    return steps


def gen_file_inspection() -> list[dict]:
    """Task family 6: File inspection."""
    tid = _task_id()
    goal = "Inspect lines 1-220 of cortex/router.py for the budget debit function"
    steps = [
        _step(
            goal, _state("diagnose", tid), {"repo": "cortex", "known_files": ["cortex/router.py"]},
            _budget(18, 20, 7, 8),
            "task started",
            '@tool → call [name: "shell.readonly", args: "sed -n \'1,220p\' cortex/router.py", risk: "read_only"]',
        ),
        _step(
            goal, _state("diagnose", tid, confidence=0.7), {"repo": "cortex"},
            _budget(17, 20, 6, 8),
            "def debit_budget(units):\n    self.remaining -= units",
            '@state → update [task_id: "' + tid + '", phase: "verify", confidence: 0.82]',
        ),
        _step(
            goal, _state("verify", tid, confidence=0.82), {"repo": "cortex"},
            _budget(16, 20, 6, 8),
            "state updated to verify",
            '@halt → answer [status: "complete", confidence: 0.82, evidence: "debit_budget function found at line 45 of router.py"]',
        ),
    ]
    return steps


def gen_test_execution() -> list[dict]:
    """Task family 7: Test execution."""
    tid = _task_id()
    goal = "Run tests/test_budget.py and confirm all tests pass"
    steps = [
        _step(
            goal, _state("execute", tid), {"repo": "cortex", "known_files": ["tests/test_budget.py"]},
            _budget(16, 20, 6, 8),
            "task started",
            '@verify → run [type: "unit_test", target: "tests/test_budget.py"]',
        ),
        _step(
            goal, _state("verify", tid, confidence=0.91), {"repo": "cortex"},
            _budget(13, 20, 5, 8),
            "3 passed, 0 failed",
            '@halt → answer [status: "complete", confidence: 0.91, evidence: "pytest tests/test_budget.py: 3 passed, 0 failed"]',
        ),
    ]
    return steps


def gen_code_patching() -> list[dict]:
    """Task family 8: One-line code patching."""
    tid = _task_id()
    goal = "Fix the off-by-one error in cortex/budget.py line 87"
    steps = [
        _step(
            goal, _state("diagnose", tid), {"repo": "cortex", "known_files": ["cortex/budget.py"]},
            _budget(18, 20, 7, 8),
            "task started",
            '@tool → call [name: "shell.readonly", args: "sed -n \'80,95p\' cortex/budget.py", risk: "read_only"]',
        ),
        _step(
            goal, _state("diagnose", tid, last_error="off-by-one at line 87"), {"repo": "cortex"},
            _budget(17, 20, 6, 8),
            "line 87: if remaining < units:  # should be <=",
            '@state → update [task_id: "' + tid + '", phase: "repair", suspect: "budget.py:87"]',
        ),
        _step(
            goal, _state("repair", tid, suspect="budget.py:87"), {"repo": "cortex"},
            _budget(16, 20, 6, 8),
            "state updated to repair",
            '@tool → call [name: "shell.patch", target: "cortex/budget.py", args: "--- a/cortex/budget.py\\n+++ b/cortex/budget.py\\n@@ -87 +87 @@\\n-        if remaining < units:\\n+        if remaining <= units:", risk: "write_limited"]',
        ),
        _step(
            goal, _state("verify", tid), {"repo": "cortex"},
            _budget(11, 20, 5, 8),
            "patch applied cleanly",
            '@verify → run [type: "unit_test", target: "tests/test_budget.py"]',
        ),
        _step(
            goal, _state("verify", tid, confidence=0.89), {"repo": "cortex"},
            _budget(8, 20, 4, 8),
            "3 passed, 0 failed",
            '@memory → write [key: "repair.lesson.budget_off_by_one", value: "use <= not < for budget exhaustion check", ttl: "persistent"]',
        ),
        _step(
            goal, _state("halt", tid, confidence=0.89), {"repo": "cortex"},
            _budget(7, 20, 4, 8),
            "lesson written",
            '@halt → answer [status: "complete", confidence: 0.89, evidence: "patch applied, tests passed, lesson recorded"]',
        ),
    ]
    return steps


def gen_schema_validation() -> list[dict]:
    """Task family 9: Schema validation."""
    tid = _task_id()
    goal = "Validate that the last SCL record in the trajectory log is schema-compliant"
    steps = [
        _step(
            goal, _state("diagnose", tid), {"repo": "cortex"},
            _budget(18, 20, 7, 8),
            "task started",
            '@verify → run [type: "schema", target: "last_scl_record"]',
        ),
        _step(
            goal, _state("verify", tid, confidence=0.95), {"repo": "cortex"},
            _budget(17, 20, 7, 8),
            "schema validation passed",
            '@halt → answer [status: "complete", confidence: 0.95, evidence: "SCL schema validation passed"]',
        ),
    ]
    return steps


def gen_halt_vs_continue() -> list[dict]:
    """Task family 10: Halt-vs-continue decisions."""
    tid = _task_id()
    goal = "Determine if the router fix is complete and safe to halt"
    steps = [
        _step(
            goal, _state("verify", tid, last_verify="failed"), {"repo": "cortex"},
            _budget(10, 20, 4, 8),
            "last test run failed",
            # Correct: do NOT halt, run tests first
            '@verify → run [type: "unit_test", target: "tests/test_router.py"]',
        ),
        _step(
            goal, _state("verify", tid, last_verify="passed", confidence=0.87), {"repo": "cortex"},
            _budget(7, 20, 3, 8),
            "2 passed, 0 failed",
            '@halt → answer [status: "complete", confidence: 0.87, evidence: "unit tests passed after router fix"]',
        ),
    ]
    return steps


def gen_rollback_after_failed_patch() -> list[dict]:
    """Task family 11: Rollback after failed patch."""
    tid = _task_id()
    goal = "Apply patch_003 to cortex/router.py and verify; rollback if regression"
    steps = [
        _step(
            goal, _state("execute", tid), {"repo": "cortex"},
            _budget(18, 20, 7, 8),
            "task started",
            '@tool → call [name: "shell.patch", target: "cortex/router.py", args: "patch_003_content", risk: "write_limited"]',
        ),
        _step(
            goal, _state("verify", tid), {"repo": "cortex"},
            _budget(13, 20, 6, 8),
            "patch applied",
            '@verify → run [type: "unit_test", target: "tests/test_router.py"]',
        ),
        _step(
            goal, _state("repair", tid, failure="test_regression", suspect="patch_003"), {"repo": "cortex"},
            _budget(10, 20, 5, 8),
            "1 failed: test_budget_debit — regression introduced",
            '@state → update [task_id: "' + tid + '", phase: "repair", failure: "test_regression", suspect: "patch_003"]',
        ),
        _step(
            goal, _state("repair", tid, failure="test_regression"), {"repo": "cortex"},
            _budget(9, 20, 5, 8),
            "state updated to repair",
            '@repair → rollback [artifact: "patch_003", reason: "introduced regression in test_budget_debit"]',
        ),
        _step(
            goal, _state("repair", tid), {"repo": "cortex"},
            _budget(6, 20, 4, 8),
            "rolled back patch_003",
            '@tool → call [name: "git.diff", args: "--stat", risk: "read_only"]',
        ),
        _step(
            goal, _state("verify", tid, confidence=0.82), {"repo": "cortex"},
            _budget(5, 20, 3, 8),
            "diff clean after rollback",
            '@verify → run [type: "unit_test", target: "tests/test_router.py"]',
        ),
        _step(
            goal, _state("verify", tid, confidence=0.88), {"repo": "cortex"},
            _budget(2, 20, 2, 8),
            "2 passed, 0 failed after rollback",
            '@memory → write [key: "repair.lesson.patch_003", value: "patch_003 caused regression; rolled back; re-verify before halt", ttl: "persistent"]',
        ),
        _step(
            goal, _state("halt", tid, confidence=0.88), {"repo": "cortex"},
            _budget(1, 20, 2, 8),
            "lesson written",
            '@halt → answer [status: "complete", confidence: 0.88, evidence: "rollback succeeded, tests passed after rollback, lesson recorded"]',
        ),
    ]
    return steps


def gen_self_repair_after_test_failure() -> list[dict]:
    """Task family 12: Self-repair after test failure."""
    tid = _task_id()
    goal = "Fix failing pytest in router budget accounting"
    steps = [
        _step(
            goal, _state("diagnose", tid, last_error="budget_used not updated after tool call", confidence=0.41),
            {"repo": "cortex", "known_files": ["cortex/router.py", "tests/test_budget.py"],
             "prior_rule": "tool calls must debit budget before execution"},
            _budget(14, 20, 6, 8),
            "pytest shows test_budget_debit fails",
            '@tool → call [name: "shell.readonly", args: "sed -n \'1,220p\' cortex/router.py", risk: "read_only"]',
        ),
        _step(
            goal, _state("diagnose", tid, confidence=0.55),
            {"repo": "cortex"},
            _budget(13, 20, 5, 8),
            "found: budget.debit called AFTER execute(action) — should be before",
            '@state → update [task_id: "' + tid + '", phase: "repair", suspect: "router.py:execute_order"]',
        ),
        _step(
            goal, _state("repair", tid, suspect="router.py:execute_order"),
            {"repo": "cortex"},
            _budget(12, 20, 5, 8),
            "state updated to repair",
            '@tool → call [name: "shell.patch", target: "cortex/router.py", args: "move budget.debit before execute(action)", risk: "write_limited"]',
        ),
        _step(
            goal, _state("verify", tid),
            {"repo": "cortex"},
            _budget(7, 20, 4, 8),
            "patch applied",
            '@verify → run [type: "unit_test", target: "tests/test_budget.py"]',
        ),
        _step(
            goal, _state("verify", tid, confidence=0.89),
            {"repo": "cortex"},
            _budget(4, 20, 3, 8),
            "3 passed, 0 failed",
            '@memory → write [key: "repair.lesson.router_budget", value: "budget must debit before tool execution", ttl: "persistent"]',
        ),
        _step(
            goal, _state("halt", tid, confidence=0.89),
            {"repo": "cortex"},
            _budget(3, 20, 3, 8),
            "lesson written",
            '@halt → answer [status: "complete", confidence: 0.89, evidence: "unit test passed after rollback and minimal patch"]',
        ),
    ]
    return steps


def gen_long_state_preservation() -> list[dict]:
    """Task family 13: State preservation across 10–30 steps."""
    tid = _task_id()
    goal = "Refactor cortex/budget.py to add a wall-clock time limit and verify all tests pass"
    steps = []
    phases = [
        ("diagnose", '@tool → call [name: "shell.readonly", args: "cat cortex/budget.py", risk: "read_only"]',
         "task started", _budget(20, 20, 8, 8)),
        ("diagnose", '@memory → read [query: "budget model"]',
         "budget.py read: 120 lines", _budget(19, 20, 7, 8)),
        ("plan", '@state → update [task_id: "' + tid + '", phase: "plan", confidence: 0.6]',
         "memory: Budget tracks units and tool_calls", _budget(18, 20, 7, 8)),
        ("plan", '@tool → call [name: "shell.readonly", args: "grep -n \'wall\' cortex/budget.py", risk: "read_only"]',
         "state updated to plan", _budget(17, 20, 7, 8)),
        ("plan", '@memory → write [key: "task.wall_clock_plan", value: "add max_wall_seconds field and elapsed check", ttl: "session"]',
         "no wall-clock code found", _budget(16, 20, 6, 8)),
        ("execute", '@tool → call [name: "shell.patch", target: "cortex/budget.py", args: "add max_wall_seconds and elapsed_seconds property", risk: "write_limited"]',
         "plan written to memory", _budget(15, 20, 6, 8)),
        ("verify", '@verify → run [type: "unit_test", target: "tests/test_budget.py"]',
         "patch applied", _budget(10, 20, 5, 8)),
        ("verify", '@tool → call [name: "git.diff", args: "--stat", risk: "read_only"]',
         "2 passed, 1 failed: test_wall_clock_limit", _budget(7, 20, 4, 8)),
        ("repair", '@state → update [task_id: "' + tid + '", phase: "repair", failure: "test_wall_clock_limit"]',
         "diff: +8 lines in budget.py", _budget(6, 20, 3, 8)),
        ("repair", '@tool → call [name: "shell.readonly", args: "grep -n \'elapsed\' cortex/budget.py", risk: "read_only"]',
         "state updated to repair", _budget(5, 20, 3, 8)),
        ("repair", '@tool → call [name: "shell.patch", target: "cortex/budget.py", args: "fix elapsed_seconds to use monotonic time", risk: "write_limited"]',
         "elapsed_seconds uses time.time() not monotonic", _budget(4, 20, 2, 8)),
        ("verify", '@verify → run [type: "unit_test", target: "tests/test_budget.py"]',
         "patch applied", _budget(0, 20, 1, 8)),
        ("halt", '@halt → answer [status: "complete", confidence: 0.91, evidence: "all 3 budget tests pass after wall-clock fix"]',
         "3 passed, 0 failed", _budget(0, 20, 0, 8)),
    ]
    for phase, target, obs, bud in phases:
        steps.append(_step(
            goal,
            _state(phase, tid),
            {"repo": "cortex", "known_files": ["cortex/budget.py", "tests/test_budget.py"]},
            bud,
            obs,
            target,
        ))
    return steps


# ---------------------------------------------------------------------------
# Negative example generators
# ---------------------------------------------------------------------------

def gen_negative_examples() -> list[dict]:
    """Generate negative training examples showing bad SCL actions."""
    tid = _task_id()
    goal = "Fix failing pytest"
    base_state = _state("diagnose", tid)
    base_budget = _budget(14, 20, 6, 8)
    base_memory = {"repo": "cortex"}

    negatives = [
        # 1. Invalid SCL syntax
        {
            "goal": goal, "state": base_state, "memory": base_memory,
            "budget": base_budget, "tool_manifest": TOOL_MANIFEST,
            "observation": "pytest fails",
            "bad_action": "@tool call [name: pytest]",
            "denial_reason": "SCL syntax error: missing → operator",
            "is_policy_violation": False,
        },
        # 2. Premature halt without evidence
        {
            "goal": goal, "state": base_state, "memory": base_memory,
            "budget": base_budget, "tool_manifest": TOOL_MANIFEST,
            "observation": "pytest fails",
            "bad_action": '@halt → answer [status: "complete"]',
            "denial_reason": "halt requires non-empty evidence field",
            "is_policy_violation": False,
        },
        # 3. Unsafe shell command
        {
            "goal": goal, "state": base_state, "memory": base_memory,
            "budget": base_budget, "tool_manifest": TOOL_MANIFEST,
            "observation": "task started",
            "bad_action": '@tool → call [name: "shell.readonly", args: "rm -rf /workspace", risk: "read_only"]',
            "denial_reason": "destructive command detected",
            "is_policy_violation": True,
        },
        # 4. Forbidden anchor
        {
            "goal": goal, "state": base_state, "memory": base_memory,
            "budget": base_budget, "tool_manifest": TOOL_MANIFEST,
            "observation": "task started",
            "bad_action": '@hardware → mutate [type: "memory", port: "/dev/mem"]',
            "denial_reason": "anchor @hardware is explicitly forbidden",
            "is_policy_violation": True,
        },
        # 5. Unregistered tool
        {
            "goal": goal, "state": base_state, "memory": base_memory,
            "budget": base_budget, "tool_manifest": TOOL_MANIFEST,
            "observation": "task started",
            "bad_action": '@tool → call [name: "shell.write", args: "echo hello > /etc/passwd", risk: "write_limited"]',
            "denial_reason": "tool 'shell.write' is not registered",
            "is_policy_violation": True,
        },
        # 6. Patch before diagnosis
        {
            "goal": goal, "state": _state("diagnose", tid), "memory": base_memory,
            "budget": base_budget, "tool_manifest": TOOL_MANIFEST,
            "observation": "pytest fails",
            "bad_action": '@tool → call [name: "shell.patch", target: "cortex/router.py", args: "random change", risk: "write_limited"]',
            "denial_reason": "patch attempted before diagnosis phase",
            "is_policy_violation": False,
        },
        # 7. Claiming success without tests
        {
            "goal": goal, "state": _state("execute", tid), "memory": base_memory,
            "budget": base_budget, "tool_manifest": TOOL_MANIFEST,
            "observation": "patch applied",
            "bad_action": '@halt → answer [status: "complete", confidence: 0.99, evidence: "I believe the fix is correct"]',
            "denial_reason": "evidence must cite verifier or test output, not belief",
            "is_policy_violation": False,
        },
        # 8. Budget overrun
        {
            "goal": goal, "state": base_state, "memory": base_memory,
            "budget": {"max_units": 20, "remaining_units": 2, "max_tool_calls": 8, "remaining_tool_calls": 1},
            "tool_manifest": TOOL_MANIFEST,
            "observation": "low budget",
            "bad_action": '@tool → call [name: "pytest", args: "tests/", risk: "verify"]',
            "denial_reason": "budget insufficient: pytest costs 3 units, only 2 remaining",
            "is_policy_violation": False,
        },
        # 9. Wrong risk tier
        {
            "goal": goal, "state": base_state, "memory": base_memory,
            "budget": base_budget, "tool_manifest": TOOL_MANIFEST,
            "observation": "task started",
            "bad_action": '@tool → call [name: "pytest", args: "tests/", risk: "read_only"]',
            "denial_reason": "declared risk 'read_only' does not match tool's tier 'verify'",
            "is_policy_violation": False,
        },
        # 10. Retry loop without new evidence
        {
            "goal": goal, "state": _state("repair", tid, last_error="same error"), "memory": base_memory,
            "budget": base_budget, "tool_manifest": TOOL_MANIFEST,
            "observation": "same error as before",
            "bad_action": '@verify → run [type: "unit_test", target: "tests/test_budget.py"]',
            "denial_reason": "retry loop detected without new evidence or state change",
            "is_policy_violation": False,
        },
    ]
    return negatives


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

TASK_GENERATORS = [
    gen_scl_parsing_repair,
    gen_tool_selection,
    gen_unsafe_action_denial,
    gen_budget_aware_selection,
    gen_memory_operations,
    gen_file_inspection,
    gen_test_execution,
    gen_code_patching,
    gen_schema_validation,
    gen_halt_vs_continue,
    gen_rollback_after_failed_patch,
    gen_self_repair_after_test_failure,
    gen_long_state_preservation,
]


def generate_dataset(count: int = 200) -> tuple[list[dict], list[dict]]:
    """
    Generate a synthetic dataset of positive and negative training samples.

    Args:
        count: Approximate number of positive samples to generate.

    Returns:
        Tuple of (positive_samples, negative_samples).
    """
    positive: list[dict] = []
    negative: list[dict] = []

    # Generate from each task family, cycling until we reach count
    gen_cycle = list(TASK_GENERATORS)
    while len(positive) < count:
        random.shuffle(gen_cycle)
        for gen_fn in gen_cycle:
            steps = gen_fn()
            positive.extend(steps)
            if len(positive) >= count:
                break

    positive = positive[:count]

    # Generate negative examples
    negative = gen_negative_examples()
    # Duplicate negatives to match positive count ratio
    while len(negative) < count // 5:
        negative.extend(gen_negative_examples())
    negative = negative[:count // 5]

    return positive, negative


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Cortex synthetic training data")
    parser.add_argument("--output", default="data", help="Output directory")
    parser.add_argument("--count", type=int, default=200, help="Number of positive samples")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    args = parser.parse_args()

    random.seed(args.seed)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Generating {args.count} positive samples...")
    positive, negative = generate_dataset(args.count)

    pos_path = output_dir / "train_positive.jsonl"
    neg_path = output_dir / "train_negative.jsonl"

    with pos_path.open("w") as f:
        for s in positive:
            f.write(json.dumps(s) + "\n")

    with neg_path.open("w") as f:
        for s in negative:
            f.write(json.dumps(s) + "\n")

    print(f"Wrote {len(positive)} positive samples to {pos_path}")
    print(f"Wrote {len(negative)} negative samples to {neg_path}")

    # Print a sample
    print("\n--- Sample positive example ---")
    print(json.dumps(positive[0], indent=2))
    print("\n--- Sample negative example ---")
    print(json.dumps(negative[0], indent=2))


if __name__ == "__main__":
    main()
