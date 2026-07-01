"""
budget.py — Cortex Budget Accounting System

Tracks and enforces bounded compute resources across a task trajectory.

Unit costs (MVP defaults):
  continue loop:        1
  memory read:          1
  file read:            1
  schema verification:  1
  unit test:            3
  patch:                5
  rollback:             3
  unsafe denied:        0  (logged only)
  incorrect halt:       10 (penalty)
  policy violation:     hard fail
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


# Default unit costs per operation type
UNIT_COSTS: dict[str, int] = {
    "loop_continue": 1,
    "memory_read": 1,
    "memory_write": 1,
    "memory_compress": 2,
    "memory_ignore": 0,
    "file_read": 1,
    "schema_verify": 1,
    "unit_test": 3,
    "patch": 5,
    "rollback": 3,
    "unsafe_denied": 0,
    "incorrect_halt_penalty": 10,
    "git_diff": 1,
    "scl_parse": 1,
    "budget_check": 0,
    "state_update": 1,
    "state_snapshot": 0,
    "halt": 0,
    "deny": 0,
}


@dataclass
class BudgetSnapshot:
    """Immutable snapshot of budget state for prompt injection."""

    max_units: int
    remaining_units: int
    used_units: int
    max_tool_calls: int
    remaining_tool_calls: int
    used_tool_calls: int
    max_steps: int
    remaining_steps: int
    elapsed_seconds: float
    max_wall_seconds: float

    def to_dict(self) -> dict:
        return {
            "max_units": self.max_units,
            "remaining_units": self.remaining_units,
            "used_units": self.used_units,
            "max_tool_calls": self.max_tool_calls,
            "remaining_tool_calls": self.remaining_tool_calls,
            "used_tool_calls": self.used_tool_calls,
            "max_steps": self.max_steps,
            "remaining_steps": self.remaining_steps,
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "max_wall_seconds": self.max_wall_seconds,
        }

    def __str__(self) -> str:
        return (
            f"units={self.remaining_units}/{self.max_units} "
            f"tool_calls={self.remaining_tool_calls}/{self.max_tool_calls} "
            f"steps={self.remaining_steps}/{self.max_steps} "
            f"elapsed={self.elapsed_seconds:.1f}s"
        )


class BudgetExhaustedError(Exception):
    """Raised when a budget limit is exceeded."""


class PolicyViolationError(Exception):
    """Raised on a hard policy violation (e.g., unsafe action attempt)."""


class Budget:
    """
    Governs bounded compute resources for a single task trajectory.

    Tracks:
      - unit budget (abstract compute cost)
      - tool call count
      - loop step count
      - wall-clock time
    """

    def __init__(
        self,
        max_units: int = 20,
        max_tool_calls: int = 8,
        max_steps: int = 30,
        max_wall_seconds: float = 300.0,
    ) -> None:
        self.max_units = max_units
        self.remaining_units = max_units
        self.used_units = 0

        self.max_tool_calls = max_tool_calls
        self.remaining_tool_calls = max_tool_calls
        self.used_tool_calls = 0

        self.max_steps = max_steps
        self.remaining_steps = max_steps
        self.used_steps = 0

        self.max_wall_seconds = max_wall_seconds
        self._start_time = time.monotonic()

        self._log: list[dict] = []

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self._start_time

    def is_exhausted(self) -> bool:
        """Return True if any hard limit has been reached."""
        return (
            self.remaining_units <= 0
            or self.remaining_tool_calls <= 0
            or self.remaining_steps <= 0
            or self.elapsed_seconds >= self.max_wall_seconds
        )

    def can_afford(self, units: int) -> bool:
        """Return True if the given unit cost can be paid."""
        return self.remaining_units >= units and not self.is_exhausted()

    def snapshot(self) -> BudgetSnapshot:
        """Return an immutable snapshot of current budget state."""
        return BudgetSnapshot(
            max_units=self.max_units,
            remaining_units=self.remaining_units,
            used_units=self.used_units,
            max_tool_calls=self.max_tool_calls,
            remaining_tool_calls=self.remaining_tool_calls,
            used_tool_calls=self.used_tool_calls,
            max_steps=self.max_steps,
            remaining_steps=self.remaining_steps,
            elapsed_seconds=self.elapsed_seconds,
            max_wall_seconds=self.max_wall_seconds,
        )

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def debit(self, units: int, reason: str = "", is_tool_call: bool = False) -> None:
        """
        Debit units from the budget.

        Args:
            units: Number of units to debit.
            reason: Human-readable reason for the debit.
            is_tool_call: Whether this debit is for a tool call.

        Raises:
            BudgetExhaustedError: If the debit would exceed remaining budget.
        """
        if units < 0:
            raise ValueError(f"Cannot debit negative units: {units}")

        if self.remaining_units < units:
            raise BudgetExhaustedError(
                f"Budget exhausted: need {units} units but only {self.remaining_units} remain"
            )

        self.remaining_units -= units
        self.used_units += units

        if is_tool_call:
            if self.remaining_tool_calls <= 0:
                raise BudgetExhaustedError("Tool call budget exhausted")
            self.remaining_tool_calls -= 1
            self.used_tool_calls += 1

        self._log.append({"units": units, "reason": reason, "is_tool_call": is_tool_call})

    def debit_step(self) -> None:
        """Debit one step from the step counter."""
        if self.remaining_steps <= 0:
            raise BudgetExhaustedError("Step budget exhausted")
        self.remaining_steps -= 1
        self.used_steps += 1

    def apply_penalty(self, penalty: int, reason: str = "") -> None:
        """Apply a penalty (e.g., for incorrect halt)."""
        self.remaining_units = max(0, self.remaining_units - penalty)
        self.used_units += penalty
        self._log.append({"units": penalty, "reason": f"PENALTY: {reason}", "is_tool_call": False})

    def record_policy_violation(self, reason: str) -> None:
        """Log a policy violation attempt (does not debit but raises)."""
        self._log.append({"units": 0, "reason": f"POLICY_VIOLATION: {reason}", "is_tool_call": False})
        raise PolicyViolationError(f"Policy violation: {reason}")

    def get_log(self) -> list[dict]:
        """Return the full debit log."""
        return list(self._log)
