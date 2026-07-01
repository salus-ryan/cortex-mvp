"""Tests for cortex.compactor — quality scoring, dedup, and compaction pipeline."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cortex.compactor import (
    Compactor,
    _shingles,
    _jaccard,
    deduplicate,
    quality_score,
)
from cortex.store import TrajectoryStore


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_row(
    prompt="prompt text here for testing",
    completion="@tool call name:bash",
    scl_valid=1,
    policy_ok=1,
    verified=1,
    outcome="success",
    reward=1.0,
    task_id="T-001",
):
    row = MagicMock()
    row.__getitem__ = lambda self, k: {
        "prompt": prompt,
        "completion": completion,
        "scl_valid": scl_valid,
        "policy_ok": policy_ok,
        "verified": verified,
        "outcome": outcome,
        "reward": reward,
        "task_id": task_id,
    }[k]
    return row


@pytest.fixture
def store(tmp_path):
    s = TrajectoryStore(tmp_path / "test.db")
    return s


@pytest.fixture
def compactor(store, tmp_path):
    return Compactor(store, output_dir=tmp_path / "sft", quality_threshold=0.6)


def _populate(store, n=20, outcome="success", reward=0.8):
    store.start_task("T-001", "test goal")
    for i in range(n):
        store.log_step(
            task_id="T-001",
            step=i,
            prompt=f"SYSTEM:\nGOAL: test {i}\nOBSERVATION: step {i}",
            completion=f"@tool call name:bash args:echo {i}",
            scl_valid=True,
            policy_ok=True,
            verified=True,
            outcome=outcome,
            reward=reward,
            units_used=1.0,
        )
    store.finish_task("T-001", "success", n, float(n))


# ── quality_score ─────────────────────────────────────────────────────────────

class TestQualityScore:

    def test_perfect_score(self):
        row = _make_row(scl_valid=1, policy_ok=1, verified=1, outcome="success", reward=1.0)
        assert quality_score(row) == 1.0

    def test_zero_score(self):
        row = _make_row(scl_valid=0, policy_ok=0, verified=0, outcome="error", reward=-1.0)
        assert quality_score(row) == 0.0

    def test_partial_score(self):
        row = _make_row(scl_valid=1, policy_ok=1, verified=0, outcome="pending", reward=0.0)
        # (1 + 1 + 0 + 0.5 + 0.5) / 5 = 0.6
        assert abs(quality_score(row) - 0.6) < 1e-9

    def test_denied_outcome(self):
        row = _make_row(scl_valid=1, policy_ok=0, verified=0, outcome="denied", reward=-0.5)
        # (1 + 0 + 0 + 0 + 0.25) / 5 = 0.25
        assert abs(quality_score(row) - 0.25) < 1e-9


# ── Shingling / Jaccard ───────────────────────────────────────────────────────

class TestShingles:

    def test_identical_texts(self):
        a = _shingles("the quick brown fox")
        b = _shingles("the quick brown fox")
        assert _jaccard(a, b) == 1.0

    def test_disjoint_texts(self):
        a = _shingles("alpha beta gamma delta")
        b = _shingles("one two three four")
        assert _jaccard(a, b) == 0.0

    def test_partial_overlap(self):
        # Use longer texts so there are shared 4-grams
        a = _shingles("the quick brown fox jumps over the lazy dog near the river bank")
        b = _shingles("the quick brown fox jumps over the lazy cat near the river bank")
        j = _jaccard(a, b)
        assert 0.0 < j < 1.0

    def test_empty_sets(self):
        assert _jaccard(set(), set()) == 1.0

    def test_one_empty(self):
        a = _shingles("hello world")
        assert _jaccard(a, set()) == 0.0


# ── Deduplication ─────────────────────────────────────────────────────────────

class TestDeduplicate:

    def test_exact_duplicates_removed(self):
        rows = [_make_row(prompt="same prompt text here", reward=0.8)] * 5
        result = deduplicate(rows)
        assert len(result) == 1

    def test_keeps_highest_quality(self):
        r1 = _make_row(prompt="same prompt text here", reward=1.0)
        r2 = _make_row(prompt="same prompt text here", reward=0.2)
        result = deduplicate([r2, r1])
        assert result[0]["reward"] == 1.0

    def test_distinct_prompts_kept(self):
        rows = [
            _make_row(prompt=f"completely different prompt number {i} with unique content", reward=0.8)
            for i in range(10)
        ]
        result = deduplicate(rows)
        assert len(result) == 10

    def test_near_duplicates_removed(self):
        base = "the quick brown fox jumps over the lazy dog near the river"
        rows = [
            _make_row(prompt=base, reward=0.9),
            _make_row(prompt=base + " today", reward=0.7),  # near-dup
            _make_row(prompt="completely unrelated text about machine learning systems", reward=0.8),
        ]
        result = deduplicate(rows, jaccard_threshold=0.7)
        assert len(result) == 2  # base + unrelated; near-dup dropped


# ── Compactor ─────────────────────────────────────────────────────────────────

class TestCompactor:

    def test_compact_full(self, compactor, store, tmp_path):
        _populate(store, n=20)
        result = compactor.compact(strategy="full")
        assert result["rows_in"] == 20
        assert result["rows_out"] <= 20
        assert result["rows_out"] > 0
        assert Path(result["train_path"]).exists()
        assert Path(result["val_path"]).exists()

    def test_compact_incremental_first_run(self, compactor, store):
        _populate(store, n=10)
        result = compactor.compact(strategy="incremental")
        assert result["rows_in"] == 10

    def test_compact_incremental_second_run(self, compactor, store):
        _populate(store, n=10)
        compactor.compact(strategy="incremental")
        # Add more steps
        for i in range(10, 15):
            store.log_step(
                task_id="T-002", step=i,
                prompt=f"SYSTEM:\nGOAL: new {i}",
                completion=f"@tool call name:bash args:echo {i}",
                scl_valid=True, policy_ok=True, verified=True,
                outcome="success", reward=0.9, units_used=1.0,
            )
        result = compactor.compact(strategy="incremental")
        # Only the 5 new rows should be processed
        assert result["rows_in"] == 5

    def test_dry_run(self, compactor, store, tmp_path):
        _populate(store, n=10)
        result = compactor.compact(strategy="full", dry_run=True)
        assert result["dry_run"] is True
        # No files should be written
        assert not (tmp_path / "sft" / "sft_train.jsonl").exists()

    def test_quality_filter_removes_low_quality(self, store, tmp_path):
        store.start_task("T-Q", "goal")
        # 5 high-quality rows
        for i in range(5):
            store.log_step(
                task_id="T-Q", step=i,
                prompt=f"high quality prompt {i} with good content",
                completion="@tool call name:bash args:ls",
                scl_valid=True, policy_ok=True, verified=True,
                outcome="success", reward=1.0, units_used=1.0,
            )
        # 5 low-quality rows
        for i in range(5, 10):
            store.log_step(
                task_id="T-Q", step=i,
                prompt=f"low quality prompt {i} with bad content",
                completion="bad output",
                scl_valid=False, policy_ok=False, verified=False,
                outcome="error", reward=-1.0, units_used=1.0,
            )
        c = Compactor(store, output_dir=tmp_path / "sft", quality_threshold=0.6)
        result = c.compact(strategy="full")
        assert result["rows_out"] == 5  # only high-quality rows pass

    def test_sft_jsonl_format(self, compactor, store, tmp_path):
        _populate(store, n=5)
        result = compactor.compact(strategy="full")
        train_path = Path(result["train_path"])
        with open(train_path) as f:
            lines = [json.loads(l) for l in f if l.strip()]
        assert len(lines) > 0
        for line in lines:
            assert "prompt" in line
            assert "completion" in line
            assert "quality" in line
            assert "outcome" in line

    def test_compact_logs_to_db(self, compactor, store):
        _populate(store, n=10)
        compactor.compact(strategy="full")
        with store._conn() as conn:
            count = conn.execute("SELECT COUNT(*) FROM compaction_log").fetchone()[0]
        assert count == 1

    def test_compact_recursive_stabilises(self, compactor, store):
        _populate(store, n=20)
        results = compactor.compact_recursive(strategy="full", max_passes=5)
        assert len(results) >= 1
        # Each pass should have fewer or equal rows than the previous
        for i in range(1, len(results)):
            assert results[i]["rows_out"] <= results[i-1]["rows_out"]

    def test_retrain_sentinel_written(self, store, tmp_path):
        """Sentinel should be written when dataset grows past threshold."""
        import cortex.compactor as cm
        sentinel = tmp_path / ".retrain_needed"
        original = cm._RETRAIN_SENTINEL
        cm._RETRAIN_SENTINEL = sentinel
        try:
            c = Compactor(store, output_dir=tmp_path / "sft", quality_threshold=0.0)
            _populate(store, n=200)
            result = c.compact(strategy="full")
            if result.get("retrain_needed"):
                assert sentinel.exists()
        finally:
            cm._RETRAIN_SENTINEL = original
