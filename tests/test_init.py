import json
from pathlib import Path

from cortex.init import CortexInit


def test_boot_status_shutdown_and_signal_log(tmp_path: Path):
    init = CortexInit(tmp_path)
    boot = init.boot()
    assert boot["status"] == "booted"
    assert "guardian" in boot["services"]
    assert boot["services"]["oracle"]["authority"] == "interpret"

    status = init.status()
    assert status["status"] == "ok"
    assert status["shutdown_supported"] is True

    shutdown = init.shutdown("test")
    assert shutdown["status"] == "shutdown"
    assert all(s["status"] == "stopped" for s in shutdown["services"].values())

    lines = (tmp_path / "ledger" / "signals.jsonl").read_text().splitlines()
    events = [json.loads(line)["event"] for line in lines]
    assert events == ["boot", "shutdown"]


def test_reap_failed_service(tmp_path: Path):
    init = CortexInit(tmp_path)
    init.boot()
    init.fail("oracle", 7)
    reaped = init.reap()
    assert reaped["reaped"] == ["oracle"]
    assert init.status()["services"]["oracle"]["status"] == "stopped"
