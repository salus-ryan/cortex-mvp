"""Tests for the Rollback module."""

import tempfile
from pathlib import Path
import pytest
from cortex.rollback import RollbackManager, Snapshot


class TestRollbackManager:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.mgr = RollbackManager(workspace=self.tmpdir)

    def test_snapshot_file(self):
        path = Path(self.tmpdir) / "test.py"
        path.write_text("original content")
        snap = self.mgr.snapshot_file(str(path), step=1)
        assert snap is not None
        assert snap.content == "original content"
        assert snap.artifact_type == "file"

    def test_snapshot_nonexistent_file(self):
        snap = self.mgr.snapshot_file("/nonexistent/file.py", step=1)
        assert snap is None

    def test_rollback_file(self):
        path = Path(self.tmpdir) / "test.py"
        path.write_text("original")
        self.mgr.snapshot_file(str(path), step=1)
        path.write_text("modified")
        assert path.read_text() == "modified"

        result = self.mgr.rollback(str(path), reason="regression", step=2)
        assert result.success
        assert path.read_text() == "original"

    def test_rollback_no_snapshot(self):
        result = self.mgr.rollback("/nonexistent/file.py", reason="test", step=1)
        assert not result.success
        assert "no snapshot" in result.reason.lower()

    def test_snapshot_state(self):
        state = {"phase": "diagnose", "confidence": 0.5}
        snap = self.mgr.snapshot_state(state, step=1)
        assert snap.artifact_type == "state"
        assert snap.content["phase"] == "diagnose"

    def test_rollback_state(self):
        state = {"phase": "diagnose"}
        self.mgr.snapshot_state(state, step=1, artifact_id="task_state")
        result = self.mgr.rollback("task_state", reason="test", step=2)
        assert result.success
        assert result.restored_to_step == 1

    def test_multiple_snapshots_restores_latest(self):
        path = Path(self.tmpdir) / "multi.py"
        path.write_text("v1")
        self.mgr.snapshot_file(str(path), step=1)
        path.write_text("v2")
        self.mgr.snapshot_file(str(path), step=2)
        path.write_text("v3")

        result = self.mgr.rollback(str(path), reason="test", step=3)
        assert result.success
        assert path.read_text() == "v2"

    def test_get_rollback_log(self):
        path = Path(self.tmpdir) / "log.py"
        path.write_text("content")
        self.mgr.snapshot_file(str(path), step=1)
        self.mgr.rollback(str(path), reason="test", step=2)
        log = self.mgr.get_rollback_log()
        assert len(log) == 1
        assert log[0]["reason"] == "test"

    def test_get_snapshot(self):
        path = Path(self.tmpdir) / "snap.py"
        path.write_text("content")
        self.mgr.snapshot_file(str(path), step=1)
        snap = self.mgr.get_snapshot(str(path))
        assert snap is not None
        assert snap.content == "content"
