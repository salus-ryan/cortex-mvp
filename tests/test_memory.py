"""Tests for the Memory module."""

import pytest
from cortex.memory import Memory, MemoryTier, TTL


class TestMemory:
    def setup_method(self):
        self.mem = Memory()

    def test_write_and_read(self):
        self.mem.write("task.test_key", "test_value", ttl="session")
        results = self.mem.read("test_key")
        assert any(e.key == "task.test_key" for e in results)

    def test_read_no_results(self):
        results = self.mem.read("nonexistent_query_xyz")
        assert results == []

    def test_write_persistent(self):
        self.mem.write("rule.test", "value", ttl="persistent")
        entry = self.mem._store.get("rule.test")
        assert entry is not None
        assert entry.ttl == TTL.PERSISTENT

    def test_tier_inference_lesson(self):
        self.mem.write("lesson.budget", "debit before execute")
        entry = self.mem._store["lesson.budget"]
        assert entry.tier == MemoryTier.EPISODIC

    def test_tier_inference_rule(self):
        self.mem.write("rule.halt", "require evidence")
        entry = self.mem._store["rule.halt"]
        assert entry.tier == MemoryTier.SEMANTIC

    def test_tier_inference_default(self):
        self.mem.write("task.observation", "pytest failed")
        entry = self.mem._store["task.observation"]
        assert entry.tier == MemoryTier.SHORT_TERM

    def test_overwrite_updates_value(self):
        self.mem.write("task.x", "old")
        self.mem.write("task.x", "new")
        assert self.mem._store["task.x"].value == "new"

    def test_compress(self):
        self.mem.write("episode.step1", "budget debit failed")
        self.mem.write("episode.step2", "rollback applied")
        result = self.mem.compress("episode", "lesson.compressed", max_tokens=20)
        assert result is not None
        assert "lesson.compressed" in self.mem._store

    def test_ignore_logs_audit(self):
        self.mem.ignore("stale observation", step=5)
        log = self.mem.get_audit_log()
        assert any(e["event_type"] == "memory_ignore" for e in log)

    def test_clear_ephemeral(self):
        self.mem.write("task.ephemeral", "temp", ttl="ephemeral")
        self.mem.write("task.session", "keep", ttl="session")
        self.mem.clear_ephemeral()
        assert "task.ephemeral" not in self.mem._store
        assert "task.session" in self.mem._store

    def test_clear_session(self):
        self.mem.write("task.session", "remove", ttl="session")
        self.mem.write("rule.keep", "keep", ttl="persistent")
        self.mem.clear_session()
        assert "task.session" not in self.mem._store
        assert "rule.keep" in self.mem._store

    def test_digest_not_empty(self):
        self.mem.write("task.obs", "observation data")
        digest = self.mem.digest("T-001")
        assert len(digest) > 0

    def test_audit_log_write(self):
        self.mem.write("task.x", "y", step=3)
        log = self.mem.get_audit_log()
        write_events = [e for e in log if e["event_type"] == "memory_write"]
        assert len(write_events) >= 1

    def test_semantic_memory_seeded(self):
        results = self.mem.read("budget")
        assert len(results) > 0  # seeded rules should be found

    def test_read_access_count(self):
        self.mem.write("task.counter", "value")
        self.mem.read("counter")
        self.mem.read("counter")
        entry = self.mem._store["task.counter"]
        assert entry.access_count == 2
