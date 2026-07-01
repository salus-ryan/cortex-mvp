"""Tests for the Budget module."""

import time
import pytest
from cortex.budget import Budget, BudgetExhaustedError, PolicyViolationError, UNIT_COSTS


class TestBudget:
    def test_initial_state(self):
        b = Budget(max_units=20, max_tool_calls=8, max_steps=30)
        assert b.remaining_units == 20
        assert b.used_units == 0
        assert b.remaining_tool_calls == 8
        assert b.remaining_steps == 30

    def test_debit_units(self):
        b = Budget(max_units=20)
        b.debit(5, reason="test")
        assert b.remaining_units == 15
        assert b.used_units == 5

    def test_debit_tool_call(self):
        b = Budget(max_units=20, max_tool_calls=8)
        b.debit(3, reason="pytest", is_tool_call=True)
        assert b.remaining_tool_calls == 7
        assert b.used_tool_calls == 1

    def test_debit_step(self):
        b = Budget(max_steps=30)
        b.debit_step()
        assert b.remaining_steps == 29
        assert b.used_steps == 1

    def test_can_afford_true(self):
        b = Budget(max_units=20)
        assert b.can_afford(5)
        assert b.can_afford(20)

    def test_can_afford_false(self):
        b = Budget(max_units=5)
        assert not b.can_afford(6)

    def test_debit_exhausted_raises(self):
        b = Budget(max_units=5)
        with pytest.raises(BudgetExhaustedError):
            b.debit(10)

    def test_debit_step_exhausted_raises(self):
        b = Budget(max_steps=1)
        b.debit_step()
        with pytest.raises(BudgetExhaustedError):
            b.debit_step()

    def test_tool_call_exhausted_raises(self):
        b = Budget(max_units=100, max_tool_calls=1)
        b.debit(1, is_tool_call=True)
        with pytest.raises(BudgetExhaustedError):
            b.debit(1, is_tool_call=True)

    def test_is_exhausted_units(self):
        b = Budget(max_units=3)
        b.debit(3)
        assert b.is_exhausted()

    def test_is_exhausted_steps(self):
        b = Budget(max_steps=1)
        b.debit_step()
        assert b.is_exhausted()

    def test_penalty(self):
        b = Budget(max_units=20)
        b.apply_penalty(10, "premature halt")
        assert b.remaining_units == 10
        assert b.used_units == 10

    def test_penalty_floor_zero(self):
        b = Budget(max_units=5)
        b.apply_penalty(100, "huge penalty")
        assert b.remaining_units == 0

    def test_negative_debit_raises(self):
        b = Budget(max_units=20)
        with pytest.raises(ValueError):
            b.debit(-1)

    def test_snapshot(self):
        b = Budget(max_units=20, max_tool_calls=8, max_steps=30)
        b.debit(5)
        snap = b.snapshot()
        assert snap.remaining_units == 15
        assert snap.used_units == 5
        assert snap.max_units == 20

    def test_snapshot_to_dict(self):
        b = Budget(max_units=20)
        d = b.snapshot().to_dict()
        assert "remaining_units" in d
        assert "max_units" in d

    def test_wall_clock_exhausted(self):
        b = Budget(max_wall_seconds=0.001)
        time.sleep(0.01)
        assert b.is_exhausted()

    def test_get_log(self):
        b = Budget(max_units=20)
        b.debit(3, reason="first")
        b.debit(2, reason="second")
        log = b.get_log()
        assert len(log) == 2
        assert log[0]["reason"] == "first"
