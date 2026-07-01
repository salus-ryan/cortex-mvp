"""Tests for cortex.store — SQLite trajectory store."""

import json
import tempfile
from pathlib import Path

import pytest

from cortex.store import TrajectoryStore


@pytest.fixture
def store(tmp_path):
    return TrajectoryStore(tmp_path / "test.db")


def _add_step(store, task_id="T-001", step=0, outcome="success", reward=0.8,
              scl_valid=True, policy_ok=True, verified=True):
    return store.log_step(
        task_id=task_id,
        step=step,
        prompt="SYSTEM:\nYou are Cortex.\n\nGOAL:\ntest\n",
        completion="@tool call name:bash args:echo hi",
        phase="act",
        goal="test goal",
        scl_valid=scl_valid,
        policy_ok=policy_ok,
        verified=verified,
        outcome=outcome,
        reward=reward,
        units_used=1.0,
        tool_name="bash",
        risk_tier="low",
        meta={"test": True},
    )


class TestTrajectoryStore:

    def test_schema_created(self, store):
        with store._conn() as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
        assert "trajectories" in tables
        assert "tasks" in tables
        assert "compaction_log" in tables

    def test_log_step_returns_id(self, store):
        row_id = _add_step(store)
        assert isinstance(row_id, int)
        assert row_id >= 1

    def test_log_multiple_steps(self, store):
        store.start_task("T-001", "test goal")
        for i in range(5):
            _add_step(store, step=i)
        rows = store.get_task_steps("T-001")
        assert len(rows) == 5
        assert [r["step"] for r in rows] == list(range(5))

    def test_start_and_finish_task(self, store):
        store.start_task("T-002", "another goal", model_ver="v1")
        store.finish_task("T-002", "success", total_steps=3, total_units=5.0)
        with store._conn() as conn:
            row = conn.execute(
                "SELECT * FROM tasks WHERE task_id='T-002'"
            ).fetchone()
        assert row["status"] == "success"
        assert row["total_steps"] == 3
        assert row["total_units"] == 5.0
        assert row["finished_at"] is not None

    def test_update_step_outcome(self, store):
        row_id = _add_step(store, outcome="pending", reward=0.0)
        store.update_step_outcome(row_id, "success", 1.0, verified=True)
        with store._conn() as conn:
            row = conn.execute(
                "SELECT * FROM trajectories WHERE id=?", (row_id,)
            ).fetchone()
        assert row["outcome"] == "success"
        assert row["reward"] == 1.0
        assert row["verified"] == 1

    def test_query_by_outcome(self, store):
        _add_step(store, task_id="T-A", step=0, outcome="success", reward=1.0)
        _add_step(store, task_id="T-A", step=1, outcome="error",   reward=-1.0)
        _add_step(store, task_id="T-A", step=2, outcome="denied",  reward=-0.5)

        success_rows = store.query(outcome="success")
        assert len(success_rows) == 1
        assert success_rows[0]["outcome"] == "success"

    def test_query_by_min_reward(self, store):
        _add_step(store, task_id="T-B", step=0, reward=0.9)
        _add_step(store, task_id="T-B", step=1, reward=0.3)
        _add_step(store, task_id="T-B", step=2, reward=-0.5)

        high = store.query(min_reward=0.5)
        assert all(r["reward"] >= 0.5 for r in high)
        assert len(high) == 1

    def test_query_by_scl_valid(self, store):
        _add_step(store, task_id="T-C", step=0, scl_valid=True)
        _add_step(store, task_id="T-C", step=1, scl_valid=False)

        valid = store.query(scl_valid=True)
        invalid = store.query(scl_valid=False)
        assert all(r["scl_valid"] == 1 for r in valid)
        assert all(r["scl_valid"] == 0 for r in invalid)

    def test_stats(self, store):
        store.start_task("T-D", "goal")
        _add_step(store, task_id="T-D", step=0, outcome="success", reward=1.0, scl_valid=True)
        _add_step(store, task_id="T-D", step=1, outcome="denied",  reward=-1.0, scl_valid=False)
        store.finish_task("T-D", "success", 2, 2.0)

        s = store.stats()
        assert s["total_steps"] == 2
        assert s["success_steps"] == 1
        assert s["denied_steps"] == 1
        assert s["scl_valid_steps"] == 1
        assert s["tasks_success"] == 1

    def test_log_compaction(self, store):
        store.log_compaction("incremental", 100, 80, "train.jsonl", "val.jsonl", "test")
        with store._conn() as conn:
            row = conn.execute(
                "SELECT * FROM compaction_log ORDER BY id DESC LIMIT 1"
            ).fetchone()
        assert row["strategy"] == "incremental"
        assert row["rows_in"] == 100
        assert row["rows_out"] == 80

    def test_meta_json_roundtrip(self, store):
        meta = {"key": "value", "nested": {"a": 1}}
        row_id = store.log_step(
            task_id="T-E", step=0,
            prompt="p", completion="c",
            meta=meta,
        )
        with store._conn() as conn:
            row = conn.execute(
                "SELECT meta FROM trajectories WHERE id=?", (row_id,)
            ).fetchone()
        assert json.loads(row["meta"]) == meta

    def test_wal_mode(self, store):
        with store._conn() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"

    def test_idempotent_start_task(self, store):
        store.start_task("T-F", "goal")
        store.start_task("T-F", "goal")  # should not raise
        with store._conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE task_id='T-F'"
            ).fetchone()[0]
        assert count == 1
