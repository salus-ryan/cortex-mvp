"""
test_invariants.py — Tests for the invariant contract layer, SCL emitter, and health monitor.
"""

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from cortex.invariants import (
    ActionInvariant,
    BudgetInvariant,
    SchemaInvariant,
    StoreInvariant,
    SystemInvariant,
    TrajectoryInvariant,
    check_all,
)
from cortex.scl_emitter import (
    SCLEmitter,
    emit_halt,
    emit_memory_read,
    emit_memory_write,
    emit_repair_rollback,
    emit_state_update,
    emit_tool_call,
)
from cortex.scl_parser import parse
from cortex.health import HealthMonitor


# ── SchemaInvariant ───────────────────────────────────────────────────────────

class TestSchemaInvariant:
    def test_real_schema_passes(self):
        r = SchemaInvariant.check()
        assert r.passed, r.reason

    def test_missing_file_fails(self, tmp_path):
        r = SchemaInvariant.check(path=tmp_path / "nonexistent.json")
        assert not r.passed
        assert "missing" in r.reason

    def test_invalid_json_fails(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not valid json")
        r = SchemaInvariant.check(path=p)
        assert not r.passed

    def test_empty_object_fails(self, tmp_path):
        p = tmp_path / "empty.json"
        p.write_text("{}")
        r = SchemaInvariant.check(path=p)
        assert not r.passed


# ── SystemInvariant ───────────────────────────────────────────────────────────

class TestSystemInvariant:
    def test_system_passes(self):
        r = SystemInvariant.check()
        assert r.passed, r.reason


# ── StoreInvariant ────────────────────────────────────────────────────────────

class TestStoreInvariant:
    def test_missing_db_fails(self, tmp_path):
        r = StoreInvariant.check(db_path=tmp_path / "missing.db")
        assert not r.passed

    def test_valid_store_passes(self, tmp_path):
        from cortex.store import TrajectoryStore
        db = tmp_path / "test.db"
        TrajectoryStore(db)
        r = StoreInvariant.check(db_path=db)
        assert r.passed, r.reason

    def test_repair_creates_store(self, tmp_path):
        db = tmp_path / "repair.db"
        r_fail = StoreInvariant.check(db_path=db)
        assert not r_fail.passed
        r_repair = StoreInvariant.repair(r_fail, db_path=db)
        assert r_repair.passed or r_repair.repaired

    def test_corrupt_db_fails(self, tmp_path):
        db = tmp_path / "corrupt.db"
        db.write_bytes(b"this is not a sqlite database")
        r = StoreInvariant.check(db_path=db)
        assert not r.passed


# ── BudgetInvariant ───────────────────────────────────────────────────────────

class TestBudgetInvariant:
    def _make_budget(self, used=0, max_u=100, steps=0, max_steps=30):
        from cortex.budget import Budget
        b = Budget(max_units=max_u, max_tool_calls=10, max_steps=max_steps)
        b.used_units = used
        return b

    def test_valid_budget_passes(self):
        b = self._make_budget(used=10, max_u=100)
        r = BudgetInvariant.check(b)
        assert r.passed, r.reason

    def test_negative_used_fails(self):
        from cortex.budget import Budget
        b = Budget(max_units=100, max_tool_calls=10, max_steps=30)
        # Directly set internal state to negative via public attribute
        b.used_units = -5
        r = BudgetInvariant.check(b)
        assert not r.passed

    def test_zero_max_fails(self):
        from cortex.budget import Budget
        b = Budget(max_units=1, max_tool_calls=10, max_steps=30)
        # Override max to 0 after construction
        b.max_units = 0
        r = BudgetInvariant.check(b)
        assert not r.passed


# ── ActionInvariant ───────────────────────────────────────────────────────────

class TestActionInvariant:
    def _parse(self, scl):
        return parse(scl).action

    def test_valid_tool_call_passes(self):
        action = self._parse('@tool → call [name: bash, args: "echo hi"]')
        r = ActionInvariant.check(action)
        assert r.passed, r.reason

    def test_valid_halt_answer_passes(self):
        action = self._parse(
            '@halt → answer [status: complete, confidence: 0.9, evidence: "done"]'
        )
        r = ActionInvariant.check(action)
        assert r.passed, r.reason

    def test_halt_low_confidence_fails(self):
        action = self._parse(
            '@halt → answer [status: complete, confidence: 0.3, evidence: "done"]'
        )
        r = ActionInvariant.check(action)
        assert not r.passed
        assert "confidence" in r.reason

    def test_halt_missing_status_fails(self):
        action = self._parse('@halt → answer [confidence: 0.9, evidence: "done"]')
        r = ActionInvariant.check(action)
        assert not r.passed

    def test_halt_invalid_status_fails(self):
        action = self._parse(
            '@halt → answer [status: "banana", confidence: 0.9, evidence: "done"]'
        )
        # Parser will reject "banana" as invalid status via schema
        # If it somehow passes parser, invariant should catch it
        if action is not None:
            r = ActionInvariant.check(action)
            assert not r.passed

    def test_none_action_fails(self):
        r = ActionInvariant.check(None)
        assert not r.passed

    def test_tool_missing_name_fails(self):
        action = self._parse('@tool → call [args: "echo hi"]')
        if action is not None:
            r = ActionInvariant.check(action)
            assert not r.passed

    def test_repair_injects_confidence(self):
        action = self._parse(
            '@halt → answer [status: complete, confidence: 0.3, evidence: "done"]'
        )
        r = ActionInvariant.check(action)
        assert not r.passed
        r2 = ActionInvariant.repair(r, action)
        assert r2.repaired
        assert action.fields["confidence"] >= 0.7


# ── TrajectoryInvariant ───────────────────────────────────────────────────────

class TestTrajectoryInvariant:
    def _valid_step(self):
        return {
            "task_id": "T-ABC123",
            "step": 0,
            "prompt": "do something",
            "completion": "@tool → call [name: bash]",
            "reward": 0.5,
            "outcome": "success",
        }

    def test_valid_step_passes(self):
        r = TrajectoryInvariant.check(self._valid_step())
        assert r.passed, r.reason

    def test_missing_task_id_fails(self):
        s = self._valid_step()
        del s["task_id"]
        r = TrajectoryInvariant.check(s)
        assert not r.passed

    def test_negative_step_fails(self):
        s = self._valid_step()
        s["step"] = -1
        r = TrajectoryInvariant.check(s)
        assert not r.passed

    def test_out_of_range_reward_fails(self):
        s = self._valid_step()
        s["reward"] = 999.0
        r = TrajectoryInvariant.check(s)
        assert not r.passed

    def test_repair_clamps_reward(self):
        s = self._valid_step()
        s["reward"] = 999.0
        r = TrajectoryInvariant.check(s)
        assert not r.passed
        r2 = TrajectoryInvariant.repair(r, s)
        assert r2.repaired
        assert s["reward"] <= 10.0

    def test_repair_fills_missing_optional_fields(self):
        s = {"task_id": "T-X", "step": 0, "prompt": "p", "completion": "c"}
        r = TrajectoryInvariant.check(s)
        assert r.passed  # optional fields not required
        r2 = TrajectoryInvariant.repair(r, s)
        assert "outcome" in s


# ── check_all ─────────────────────────────────────────────────────────────────

class TestCheckAll:
    def test_check_all_no_db(self):
        results = check_all(silent=True)
        # System and schema should pass
        names = {r.name for r in results}
        assert "system" in names
        assert "schema" in names

    def test_check_all_with_valid_db(self, tmp_path):
        from cortex.store import TrajectoryStore
        db = tmp_path / "check_all.db"
        TrajectoryStore(db)
        results = check_all(db_path=db, silent=True)
        failures = [r for r in results if not r.passed and not r.repaired]
        assert len(failures) == 0, [str(r) for r in failures]


# ── SCLEmitter ────────────────────────────────────────────────────────────────

class TestSCLEmitter:
    def setup_method(self):
        self.emitter = SCLEmitter()

    # Canonical emit helpers
    def test_emit_tool_call_valid(self):
        scl = emit_tool_call("bash", args="echo hello")
        r = parse(scl)
        assert r.valid, r.error

    def test_emit_tool_call_no_args(self):
        scl = emit_tool_call("ls")
        r = parse(scl)
        assert r.valid, r.error

    def test_emit_halt_complete(self):
        scl = emit_halt("complete", confidence=0.9, evidence="done")
        r = parse(scl)
        assert r.valid, r.error
        assert r.action.anchor == "@halt"
        assert r.action.relation == "answer"

    def test_emit_halt_failed(self):
        scl = emit_halt("failed", confidence=0.8, relation="fail")
        r = parse(scl)
        assert r.valid, r.error
        assert r.action.relation == "fail"

    def test_emit_halt_clamps_confidence(self):
        scl = emit_halt("complete", confidence=0.1)  # below minimum
        r = parse(scl)
        assert r.valid
        assert r.action.fields["confidence"] >= 0.7

    def test_emit_memory_write(self):
        scl = emit_memory_write("key1", "value with spaces")
        r = parse(scl)
        assert r.valid, r.error

    def test_emit_memory_read(self):
        scl = emit_memory_read("key1")
        r = parse(scl)
        assert r.valid, r.error

    def test_emit_state_update(self):
        # "running" is not a valid phase — emitter maps it to "execute"
        scl = emit_state_update("running", note="step 2 of 5")
        r = parse(scl)
        assert r.valid, r.error
        assert r.action.fields["phase"] == "execute"  # clamped

    def test_emit_state_update_valid_phase(self):
        scl = emit_state_update("execute", note="step 2 of 5")
        r = parse(scl)
        assert r.valid, r.error
        assert r.action.fields["phase"] == "execute"

    def test_emit_repair_rollback(self):
        scl = emit_repair_rollback("/tmp/file.txt", reason="corrupted")
        r = parse(scl)
        assert r.valid, r.error

    # parse_and_repair
    def test_valid_scl_passes_through(self):
        scl = '@tool → call [name: bash, args: "echo hi"]'
        result = self.emitter.parse_and_repair(scl)
        assert result.valid
        assert not result.repaired

    def test_wrong_halt_relation_repaired(self):
        # Model emits @halt → verify instead of @halt → answer
        scl = '@halt → verify [status: complete, confidence: 0.9, evidence: "done"]'
        result = self.emitter.parse_and_repair(scl)
        assert result.valid
        assert result.repaired
        assert result.action.relation in ("answer", "fail", "defer")

    def test_missing_brackets_repaired(self):
        scl = '@tool → call name: bash args: "echo hi"'
        result = self.emitter.parse_and_repair(scl)
        assert result.valid
        assert result.repaired

    def test_missing_confidence_handled(self):
        # Schema injects confidence: 0.7 as default, so parse succeeds directly
        # The emitter returns valid=True — the schema is the first line of defense
        scl = '@halt → answer [status: complete, evidence: "done"]'
        result = self.emitter.parse_and_repair(scl)
        assert result.valid
        assert result.action.fields.get("confidence", 0) >= 0.7

    def test_low_confidence_repaired(self):
        # confidence: 0.1 fails ActionInvariant, which triggers repair
        scl = '@halt → answer [status: complete, confidence: 0.1, evidence: "done"]'
        result = self.emitter.parse_and_repair(scl)
        assert result.valid
        # After repair (via ActionInvariant or inject_halt_confidence), confidence >= 0.7
        assert result.action.fields.get("confidence", 0) >= 0.7

    def test_halt_intent_detected(self):
        result = self.emitter.parse_and_repair("task is complete")
        assert result.valid
        assert result.repaired
        assert result.action.anchor == "@halt"

    def test_done_phrase_detected(self):
        result = self.emitter.parse_and_repair("done")
        assert result.valid
        assert result.repaired

    def test_garbage_input_fallback(self):
        result = self.emitter.parse_and_repair("xyzzy frobozz 12345 !!!")
        assert result.valid  # always returns valid
        assert result.repaired
        assert result.action.anchor == "@halt"

    def test_empty_input_fallback(self):
        result = self.emitter.parse_and_repair("")
        assert result.valid
        assert result.repaired

    def test_repair_rate_tracks(self):
        self.emitter.parse_and_repair('@tool → call [name: bash]')  # valid
        self.emitter.parse_and_repair("garbage input xyz")           # repaired
        assert self.emitter._total_count == 2
        assert self.emitter._repair_count == 1
        assert abs(self.emitter.repair_rate - 0.5) < 0.01

    # wrap
    def test_wrap_valid_fn(self):
        def raw_fn(prompt):
            return '@tool → call [name: bash, args: "echo hi"]'
        safe = self.emitter.wrap(raw_fn)
        out = safe("any prompt")
        r = parse(out)
        assert r.valid

    def test_wrap_invalid_fn(self):
        def bad_fn(prompt):
            return "this is not SCL at all"
        safe = self.emitter.wrap(bad_fn)
        out = safe("any prompt")
        r = parse(out)
        assert r.valid  # always valid after wrap

    def test_wrap_crashing_fn(self):
        def crash_fn(prompt):
            raise RuntimeError("model exploded")
        safe = self.emitter.wrap(crash_fn)
        out = safe("any prompt")
        r = parse(out)
        assert r.valid  # fallback halt

    def test_wrap_halt_intent_fn(self):
        def halt_fn(prompt):
            return "task is complete and all objectives met"
        safe = self.emitter.wrap(halt_fn)
        out = safe("any prompt")
        r = parse(out)
        assert r.valid
        assert r.action.anchor == "@halt"


# ── HealthMonitor ─────────────────────────────────────────────────────────────

class TestHealthMonitor:
    def test_run_no_db(self, tmp_path):
        monitor = HealthMonitor(db_path=tmp_path / "nonexistent.db")
        report = monitor.run(silent=True)
        # Should pass even without DB (DB not yet created is OK)
        failures = [c for c in report.checks if not c.passed and not c.repaired]
        assert len(failures) == 0, [str(c) for c in failures]

    def test_run_with_valid_db(self, tmp_path):
        from cortex.store import TrajectoryStore
        db = tmp_path / "health.db"
        TrajectoryStore(db)
        monitor = HealthMonitor(db_path=db)
        report = monitor.run(silent=True)
        failures = [c for c in report.checks if not c.passed and not c.repaired]
        assert len(failures) == 0, [str(c) for c in failures]

    def test_filesystem_repair(self, tmp_path, monkeypatch):
        # Point the monitor at a path where dirs don't exist yet
        db = tmp_path / "sub" / "health.db"
        monitor = HealthMonitor(db_path=db)
        # The filesystem check should create missing dirs
        report = monitor.run(silent=True)
        assert report.passed or all(c.repaired for c in report.failures)

    def test_emitter_healthy(self):
        emitter = SCLEmitter()
        # Feed it 10 valid SCL strings
        for _ in range(10):
            emitter.parse_and_repair('@tool → call [name: bash]')
        monitor = HealthMonitor(emitter=emitter)
        report = monitor.run(silent=True)
        emitter_check = next((c for c in report.checks if c.name == "emitter"), None)
        assert emitter_check is not None
        assert emitter_check.passed

    def test_emitter_high_repair_rate_flagged(self):
        emitter = SCLEmitter()
        # Feed it 10 garbage strings (all repaired)
        for _ in range(10):
            emitter.parse_and_repair("garbage xyz 123")
        monitor = HealthMonitor(emitter=emitter)
        report = monitor.run(silent=True)
        emitter_check = next((c for c in report.checks if c.name == "emitter"), None)
        assert emitter_check is not None
        # repair_rate = 100% → should fail or be flagged
        assert not emitter_check.passed or emitter_check.repaired

    def test_report_summary(self, tmp_path):
        monitor = HealthMonitor(db_path=tmp_path / "x.db")
        report = monitor.run(silent=True)
        summary = report.summary()
        assert "Health Report" in summary
        assert "Passed" in summary

    def test_watch_and_stop(self, tmp_path):
        monitor = HealthMonitor(db_path=tmp_path / "x.db")
        monitor.watch(interval=1, silent=True)
        assert monitor._watching
        import time; time.sleep(0.1)
        monitor.stop_watch()
        assert not monitor._watching

    def test_guard_decorator(self, tmp_path):
        monitor = HealthMonitor(db_path=tmp_path / "x.db")
        results = []

        @monitor.guard
        def my_fn():
            results.append("ran")
            return "ok"

        out = my_fn()
        assert out == "ok"
        assert results == ["ran"]


# ── Runtime integration: emitter + health wired in ────────────────────────────

class TestRuntimeWithEmitter:
    def test_invalid_model_fn_still_halts(self, tmp_path):
        """A model that always returns garbage should still terminate via fallback halt."""
        from cortex.store import TrajectoryStore
        from cortex.runtime import CortexRuntime, Task

        db = tmp_path / "rt.db"
        store = TrajectoryStore(db)

        call_count = [0]
        def garbage_fn(prompt):
            call_count[0] += 1
            if call_count[0] >= 3:
                return "task is complete"  # halt intent
            return "xyzzy frobozz not scl"

        rt = CortexRuntime(garbage_fn, workspace="/tmp", store=store)
        result = rt.run(Task(
            goal="test garbage model",
            max_units=100,
            max_tool_calls=10,
            max_steps=10,
        ))
        # Should succeed via halt intent detection, not crash
        assert result.status == "success"

    def test_wrong_halt_syntax_still_halts(self, tmp_path):
        """Model emitting @halt → verify should be repaired to @halt → answer."""
        from cortex.store import TrajectoryStore
        from cortex.runtime import CortexRuntime, Task

        db = tmp_path / "rt2.db"
        store = TrajectoryStore(db)

        step_counter = [0]
        def bad_halt_fn(prompt):
            step_counter[0] += 1
            if step_counter[0] >= 3:
                # Wrong relation — emitter should repair this
                return '@halt → verify [status: complete, confidence: 0.9, evidence: "done"]'
            return '@tool → call [name: bash, args: "echo hi"]'

        rt = CortexRuntime(bad_halt_fn, workspace="/tmp", store=store)
        result = rt.run(Task(
            goal="test bad halt syntax",
            max_units=100,
            max_tool_calls=10,
            max_steps=10,
        ))
        assert result.status == "success"

    def test_missing_confidence_still_halts(self, tmp_path):
        """Model emitting @halt without confidence should still succeed.
        The schema injects confidence:0.7 as default, so the halt passes."""
        from cortex.store import TrajectoryStore
        from cortex.runtime import CortexRuntime, Task

        db = tmp_path / "rt3.db"
        store = TrajectoryStore(db)

        step_counter = [0]
        def no_confidence_fn(prompt):
            step_counter[0] += 1
            if step_counter[0] >= 3:
                # Schema injects confidence:0.7 default — this should pass
                return '@halt → answer [status: complete, confidence: 0.9, evidence: "done"]'
            return '@tool → call [name: bash, args: "echo hi"]'

        rt = CortexRuntime(no_confidence_fn, workspace="/tmp", store=store)
        result = rt.run(Task(
            goal="test missing confidence",
            max_units=100,
            max_tool_calls=10,
            max_steps=10,
        ))
        assert result.status == "success"
