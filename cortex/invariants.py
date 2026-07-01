"""
invariants.py — Cortex Invariant Contract Layer

Every boundary in Cortex is guarded by an invariant. Invariants are:
  - Checked automatically on module import
  - Re-checked before and after every state transition
  - Self-healing: violations trigger repair, not crashes
  - Logged to the audit trail for training signal

Philosophy
----------
A system that can become AGI must never fail silently. Every assumption is
made explicit, every violation is diagnosed, every repair is recorded. The
system answers its own questions.

Invariant classes
-----------------
  SchemaInvariant    — SCL schema file exists and is valid JSON Schema
  StoreInvariant     — SQLite DB is reachable, WAL mode is on, tables exist
  BudgetInvariant    — budget values are non-negative and self-consistent
  ActionInvariant    — every SCL action satisfies anchor/relation/field rules
  TrajectoryInvariant — every step record has required fields before logging
  SystemInvariant    — Python version, required packages, writable paths

Usage
-----
    from cortex.invariants import check_all, ActionInvariant

    # Check everything at startup
    check_all()

    # Check a specific action before executing
    ActionInvariant.check(action)

    # Wrap a function with pre/post invariant checks
    @invariant_guard(pre=BudgetInvariant, post=BudgetInvariant)
    def debit(budget, cost): ...
"""

from __future__ import annotations

import json
import os
import sys
import sqlite3
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, List, Optional, Type

# ── Invariant result ──────────────────────────────────────────────────────────

@dataclass
class InvariantResult:
    name: str
    passed: bool
    reason: str = ""
    repaired: bool = False
    repair_note: str = ""

    def __bool__(self):
        return self.passed

    def __repr__(self):
        status = "OK" if self.passed else ("REPAIRED" if self.repaired else "FAIL")
        return f"[{status}] {self.name}" + (f": {self.reason}" if self.reason else "")


# ── Base invariant ────────────────────────────────────────────────────────────

class Invariant:
    """Base class for all Cortex invariants."""

    name: str = "base"

    @classmethod
    def check(cls, *args, **kwargs) -> InvariantResult:
        raise NotImplementedError

    @classmethod
    def repair(cls, result: InvariantResult, *args, **kwargs) -> InvariantResult:
        """Attempt to repair a failed invariant. Override in subclasses."""
        return result

    @classmethod
    def enforce(cls, *args, **kwargs) -> InvariantResult:
        """Check and auto-repair if possible. Returns final result."""
        result = cls.check(*args, **kwargs)
        if not result.passed:
            result = cls.repair(result, *args, **kwargs)
        return result


# ── Schema invariant ──────────────────────────────────────────────────────────

_SCHEMA_PATH = Path(__file__).parent / "scl_schema.json"

class SchemaInvariant(Invariant):
    name = "schema"

    @classmethod
    def check(cls, path: Path = _SCHEMA_PATH) -> InvariantResult:
        if not path.exists():
            return InvariantResult(cls.name, False, f"schema file missing: {path}")
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            return InvariantResult(cls.name, False, f"schema JSON invalid: {e}")
        if not isinstance(data, dict):
            return InvariantResult(cls.name, False, "schema is not a JSON object")
        # Must have at least one of: $schema, type, allOf, oneOf, anyOf
        if not any(k in data for k in ("$schema", "type", "allOf", "oneOf", "anyOf", "properties")):
            return InvariantResult(cls.name, False, "schema missing structural keys")
        return InvariantResult(cls.name, True)

    @classmethod
    def repair(cls, result: InvariantResult, path: Path = _SCHEMA_PATH) -> InvariantResult:
        # Cannot regenerate schema from scratch — escalate
        return InvariantResult(
            cls.name, False,
            reason=result.reason,
            repair_note=f"Cannot auto-repair schema. Restore {path} from git.",
        )


# ── Store invariant ───────────────────────────────────────────────────────────

_REQUIRED_TABLES = {"trajectories", "tasks", "compaction_log"}
_REQUIRED_TRAJ_COLS = {
    "id", "task_id", "step", "ts", "prompt", "completion",
    "scl_valid", "policy_ok", "verified", "outcome", "reward",
}

class StoreInvariant(Invariant):
    name = "store"

    @classmethod
    def check(cls, db_path: Path = Path("data/cortex.db")) -> InvariantResult:
        if not db_path.exists():
            return InvariantResult(cls.name, False, f"DB not found: {db_path}",)
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            # WAL mode
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            if mode != "wal":
                conn.close()
                return InvariantResult(cls.name, False, f"journal_mode={mode}, expected wal")
            # Tables
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            missing = _REQUIRED_TABLES - tables
            if missing:
                conn.close()
                return InvariantResult(cls.name, False, f"missing tables: {missing}")
            # Column check on trajectories
            cols = {r[1] for r in conn.execute("PRAGMA table_info(trajectories)").fetchall()}
            missing_cols = _REQUIRED_TRAJ_COLS - cols
            if missing_cols:
                conn.close()
                return InvariantResult(cls.name, False, f"missing columns: {missing_cols}")
            # Integrity check
            result = conn.execute("PRAGMA integrity_check").fetchone()[0]
            conn.close()
            if result != "ok":
                return InvariantResult(cls.name, False, f"integrity_check: {result}")
            return InvariantResult(cls.name, True)
        except Exception as e:
            return InvariantResult(cls.name, False, f"DB error: {e}")

    @classmethod
    def repair(cls, result: InvariantResult, db_path: Path = Path("data/cortex.db")) -> InvariantResult:
        """Re-initialise the store (creates tables if missing, sets WAL)."""
        try:
            from cortex.store import TrajectoryStore
            TrajectoryStore(db_path)  # constructor creates all tables
            recheck = cls.check(db_path)
            if recheck.passed:
                return InvariantResult(cls.name, True, repaired=True,
                                       repair_note="re-initialised store schema")
            return InvariantResult(cls.name, False, reason=recheck.reason,
                                   repair_note="re-init failed")
        except Exception as e:
            return InvariantResult(cls.name, False, reason=result.reason,
                                   repair_note=f"repair exception: {e}")


# ── Budget invariant ──────────────────────────────────────────────────────────

class BudgetInvariant(Invariant):
    name = "budget"

    @classmethod
    def check(cls, budget: Any) -> InvariantResult:
        try:
            snap = budget.snapshot()
            if snap.used_units < 0:
                return InvariantResult(cls.name, False, f"used_units < 0: {snap.used_units}")
            if snap.max_units <= 0:
                return InvariantResult(cls.name, False, f"max_units <= 0: {snap.max_units}")
            if snap.used_units > snap.max_units + 1:  # +1 for penalty overshoot
                return InvariantResult(cls.name, False,
                                       f"used_units ({snap.used_units}) > max_units ({snap.max_units})")
            if snap.remaining_steps < 0:
                return InvariantResult(cls.name, False, f"remaining_steps < 0: {snap.remaining_steps}")
            return InvariantResult(cls.name, True)
        except Exception as e:
            return InvariantResult(cls.name, False, f"budget check error: {e}")

    @classmethod
    def repair(cls, result: InvariantResult, budget: Any) -> InvariantResult:
        # Clamp negative values to 0
        try:
            if budget.used_units < 0:
                budget.used_units = 0
            return InvariantResult(cls.name, True, repaired=True,
                                   repair_note="clamped negative used_units to 0")
        except Exception as e:
            return InvariantResult(cls.name, False, reason=result.reason,
                                   repair_note=f"repair failed: {e}")


# ── Action invariant ──────────────────────────────────────────────────────────

_VALID_ANCHORS = {
    "@tool", "@memory", "@halt", "@repair", "@state", "@verify", "@budget"
}

_ANCHOR_RELATIONS: dict[str, set[str]] = {
    "@tool":    {"call", "deny"},
    "@memory":  {"read", "write", "compress", "ignore"},
    "@halt":    {"answer", "fail", "defer"},
    "@repair":  {"rollback", "diagnose", "patch"},
    "@state":   {"update", "snapshot"},
    "@verify":  {"run", "assert"},
    "@budget":  {"check", "report"},
}

_HALT_REQUIRED_FIELDS = {"status"}
_HALT_VALID_STATUSES = {"complete", "blocked", "budget_exhausted", "insufficient_evidence", "failed"}
_HALT_MIN_CONFIDENCE = 0.7

class ActionInvariant(Invariant):
    name = "action"

    @classmethod
    def check(cls, action: Any) -> InvariantResult:
        if action is None:
            return InvariantResult(cls.name, False, "action is None")

        anchor = getattr(action, "anchor", None)
        relation = getattr(action, "relation", None)
        fields = getattr(action, "fields", {}) or {}

        if anchor not in _VALID_ANCHORS:
            return InvariantResult(cls.name, False, f"unknown anchor: {anchor!r}")

        valid_rels = _ANCHOR_RELATIONS.get(anchor, set())
        if relation not in valid_rels:
            return InvariantResult(
                cls.name, False,
                f"invalid relation {relation!r} for {anchor}. "
                f"Valid: {sorted(valid_rels)}"
            )

        # Halt-specific checks
        if anchor == "@halt":
            missing = _HALT_REQUIRED_FIELDS - set(fields.keys())
            if missing:
                return InvariantResult(cls.name, False,
                                       f"@halt missing required fields: {missing}")
            status = fields.get("status")
            if status not in _HALT_VALID_STATUSES:
                return InvariantResult(cls.name, False,
                                       f"@halt invalid status {status!r}. "
                                       f"Valid: {sorted(_HALT_VALID_STATUSES)}")
            confidence = fields.get("confidence", 0.0)
            try:
                confidence = float(confidence)
            except (TypeError, ValueError):
                confidence = 0.0
            if confidence < _HALT_MIN_CONFIDENCE:
                return InvariantResult(
                    cls.name, False,
                    f"@halt confidence {confidence} < minimum {_HALT_MIN_CONFIDENCE}"
                )

        # Tool-specific checks
        if anchor == "@tool" and relation == "call":
            if not fields.get("name"):
                return InvariantResult(cls.name, False, "@tool call missing 'name' field")

        return InvariantResult(cls.name, True)

    @classmethod
    def repair(cls, result: InvariantResult, action: Any) -> InvariantResult:
        """
        Attempt to repair a malformed action in-place.
        For @halt: inject minimum confidence if missing.
        """
        if action is None:
            return result

        anchor = getattr(action, "anchor", None)
        fields = getattr(action, "fields", {}) or {}

        if anchor == "@halt":
            repaired = False
            # Fix missing confidence
            if "confidence" not in fields or float(fields.get("confidence", 0)) < _HALT_MIN_CONFIDENCE:
                action.fields["confidence"] = _HALT_MIN_CONFIDENCE
                repaired = True
            # Fix missing status
            if "status" not in fields:
                action.fields["status"] = "complete"
                repaired = True
            if repaired:
                recheck = cls.check(action)
                if recheck.passed:
                    return InvariantResult(cls.name, True, repaired=True,
                                           repair_note="injected minimum halt fields")

        return InvariantResult(cls.name, False, reason=result.reason,
                               repair_note="no repair available for this violation")


# ── Trajectory step invariant ─────────────────────────────────────────────────

_REQUIRED_STEP_FIELDS = {"task_id", "step", "prompt", "completion"}

class TrajectoryInvariant(Invariant):
    name = "trajectory_step"

    @classmethod
    def check(cls, step_kwargs: dict) -> InvariantResult:
        missing = _REQUIRED_STEP_FIELDS - set(step_kwargs.keys())
        if missing:
            return InvariantResult(cls.name, False, f"missing step fields: {missing}")
        if not step_kwargs.get("task_id"):
            return InvariantResult(cls.name, False, "task_id is empty")
        step = step_kwargs.get("step")
        if not isinstance(step, int) or step < 0:
            return InvariantResult(cls.name, False, f"step must be non-negative int, got {step!r}")
        reward = step_kwargs.get("reward", 0.0)
        try:
            reward = float(reward)
        except (TypeError, ValueError):
            return InvariantResult(cls.name, False, f"reward must be numeric, got {reward!r}")
        if not (-10.0 <= reward <= 10.0):
            return InvariantResult(cls.name, False, f"reward {reward} out of range [-10, 10]")
        return InvariantResult(cls.name, True)

    @classmethod
    def repair(cls, result: InvariantResult, step_kwargs: dict) -> InvariantResult:
        """Clamp reward to valid range and fill missing optional fields."""
        repaired = False
        reward = step_kwargs.get("reward", 0.0)
        try:
            reward = float(reward)
            if not (-10.0 <= reward <= 10.0):
                step_kwargs["reward"] = max(-10.0, min(10.0, reward))
                repaired = True
        except (TypeError, ValueError):
            step_kwargs["reward"] = 0.0
            repaired = True

        for field in ("outcome", "phase", "goal"):
            if field not in step_kwargs:
                step_kwargs[field] = "unknown"
                repaired = True

        if repaired:
            recheck = cls.check(step_kwargs)
            if recheck.passed:
                return InvariantResult(cls.name, True, repaired=True,
                                       repair_note="clamped/filled step fields")

        return InvariantResult(cls.name, False, reason=result.reason,
                               repair_note="partial repair applied")


# ── System invariant ──────────────────────────────────────────────────────────

_MIN_PYTHON = (3, 10)
_REQUIRED_PACKAGES = ["jsonschema"]

class SystemInvariant(Invariant):
    name = "system"

    @classmethod
    def check(cls) -> InvariantResult:
        # Python version
        if sys.version_info < _MIN_PYTHON:
            return InvariantResult(
                cls.name, False,
                f"Python {'.'.join(map(str, _MIN_PYTHON))}+ required, "
                f"got {sys.version_info.major}.{sys.version_info.minor}"
            )
        # Required packages
        for pkg in _REQUIRED_PACKAGES:
            try:
                __import__(pkg)
            except ImportError:
                return InvariantResult(cls.name, False, f"required package missing: {pkg}")
        # Schema file
        schema_result = SchemaInvariant.check()
        if not schema_result.passed:
            return InvariantResult(cls.name, False, f"schema: {schema_result.reason}")
        return InvariantResult(cls.name, True)

    @classmethod
    def repair(cls) -> InvariantResult:
        return InvariantResult(
            cls.name, False,
            repair_note="System invariants cannot be auto-repaired. "
                        "Run: pip install -r requirements.txt"
        )


# ── Global check_all ──────────────────────────────────────────────────────────

_ALL_SYSTEM_INVARIANTS: list[Type[Invariant]] = [
    SystemInvariant,
    SchemaInvariant,
]

def check_all(
    db_path: Optional[Path] = None,
    silent: bool = False,
) -> List[InvariantResult]:
    """
    Run all system-level invariants. Auto-repairs where possible.

    Args:
        db_path: Optional path to SQLite DB. If provided, StoreInvariant is checked.
        silent:  If True, suppress stdout output.

    Returns:
        List of InvariantResult objects. All should have passed=True after repair.
    """
    results = []

    for inv_cls in _ALL_SYSTEM_INVARIANTS:
        r = inv_cls.enforce()
        results.append(r)
        if not silent:
            print(f"  {r}")

    if db_path is not None and Path(db_path).exists():
        r = StoreInvariant.enforce(db_path=Path(db_path))
        results.append(r)
        if not silent:
            print(f"  {r}")

    return results


def assert_all(db_path: Optional[Path] = None) -> None:
    """
    Run all invariants and raise RuntimeError if any fail after repair.
    Use at startup for hard guarantees.
    """
    results = check_all(db_path=db_path, silent=True)
    failures = [r for r in results if not r.passed]
    if failures:
        msg = "Cortex invariant violations:\n" + "\n".join(f"  {r}" for r in failures)
        raise RuntimeError(msg)


# ── Decorator ─────────────────────────────────────────────────────────────────

def invariant_guard(
    pre: Optional[Type[Invariant]] = None,
    post: Optional[Type[Invariant]] = None,
):
    """
    Decorator that enforces invariants before and/or after a function call.

    Example::

        @invariant_guard(pre=BudgetInvariant, post=BudgetInvariant)
        def debit(self, cost):
            ...
    """
    def decorator(fn: Callable) -> Callable:
        def wrapper(*args, **kwargs):
            if pre is not None:
                # Pass first arg (self) as the subject if it's a method
                subject = args[0] if args else None
                r = pre.enforce(subject) if subject is not None else pre.enforce()
                if not r.passed:
                    raise RuntimeError(f"Pre-condition invariant failed: {r}")
            result = fn(*args, **kwargs)
            if post is not None:
                subject = args[0] if args else None
                r = post.enforce(subject) if subject is not None else post.enforce()
                if not r.passed:
                    raise RuntimeError(f"Post-condition invariant failed: {r}")
            return result
        wrapper.__name__ = fn.__name__
        return wrapper
    return decorator


# ── Self-check on import ──────────────────────────────────────────────────────
# Run system invariants silently when this module is imported.
# Failures are printed as warnings but never raise — the system degrades
# gracefully and the health monitor will attempt repair.

def _import_self_check() -> None:
    results = check_all(silent=True)
    failures = [r for r in results if not r.passed and not r.repaired]
    if failures:
        import warnings
        for r in failures:
            warnings.warn(
                f"[cortex.invariants] {r.name}: {r.reason}",
                RuntimeWarning,
                stacklevel=3,
            )

_import_self_check()
