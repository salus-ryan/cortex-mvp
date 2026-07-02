import json
import sys
import time
from pathlib import Path

import cortex.pid1 as pid1
from cortex.pid1 import ChildSpec, CortexPID1


def patch_paths(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(pid1, "ROOT", tmp_path)
    monkeypatch.setattr(pid1, "RUNTIME", tmp_path / "runtime")
    monkeypatch.setattr(pid1, "LEDGER", tmp_path / "ledger")
    monkeypatch.setattr(pid1, "PID1_STATUS", tmp_path / "runtime" / "pid1.json")
    monkeypatch.setattr(pid1, "PID1_SIGNALS", tmp_path / "ledger" / "pid1-signals.jsonl")


def test_pid1_writes_status(monkeypatch, tmp_path):
    patch_paths(monkeypatch, tmp_path)
    supervisor = CortexPID1(specs=[])
    supervisor.write_status()
    data = json.loads((tmp_path / "runtime" / "pid1.json").read_text())
    assert data["pid"] > 0
    assert data["is_pid1"] is False
    assert data["children"] == {}


def test_pid1_reaps_exited_child(monkeypatch, tmp_path):
    patch_paths(monkeypatch, tmp_path)
    spec = ChildSpec(
        name="short",
        command=[sys.executable, "-c", "import sys; sys.exit(0)"],
        restart="never",
    )
    supervisor = CortexPID1(specs=[spec])
    supervisor.start_child(spec)
    time.sleep(0.2)
    supervisor.reap()
    assert supervisor.states["short"].status == "exited"
    events = (tmp_path / "ledger" / "pid1-signals.jsonl").read_text()
    assert "child_started" in events
    assert "child_reaped" in events


def test_pid1_bounded_restart_on_failure(monkeypatch, tmp_path):
    patch_paths(monkeypatch, tmp_path)
    spec = ChildSpec(
        name="flaky",
        command=[sys.executable, "-c", "import sys; sys.exit(7)"],
        restart="on-failure",
        max_restarts=1,
    )
    supervisor = CortexPID1(specs=[spec])
    supervisor.start_child(spec)
    for _ in range(5):
        time.sleep(0.2)
        supervisor.reap()
    assert supervisor.states["flaky"].restarts == 1
