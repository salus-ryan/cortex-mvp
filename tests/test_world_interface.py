import json
from pathlib import Path

from cortex.world_interface import WorldInterfaceService


def test_world_event_bus_records_durable_events(tmp_path: Path):
    svc = WorldInterfaceService(tmp_path)
    rec = svc.record_event("test", "observation", {"value": 1})
    report = svc.event_bus_report()

    assert rec["status"] == "event_recorded"
    assert rec["record"]["may_execute"] is False
    assert report["status"] == "event_bus_report"
    assert report["event_count"] == 1
    assert report["event_type_counts"] == {"observation": 1}
    assert (tmp_path / "ledger" / "events.jsonl").exists()
    assert report["may_execute"] is False


def test_sensory_adapters_are_evidence_based(tmp_path: Path):
    for path in ["cortex/web.py", "cortex/repo_service.py", "ledger", "mobile/index.html"]:
        target = tmp_path / path
        if path == "ledger":
            target.mkdir(parents=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("seed")

    report = WorldInterfaceService(tmp_path).sensory_adapters()

    assert report["status"] == "sensory_adapters"
    assert report["enabled_count"] == 4
    assert all(adapter["evidence_exists"] is True for adapter in report["adapters"])
    assert all(adapter["may_execute"] is False for adapter in report["adapters"])
    assert report["may_execute"] is False


def test_operator_console_summarizes_pending_review(tmp_path: Path):
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    (ledger / "model-proposals.jsonl").write_text(json.dumps({"id": "proposal_1", "may_execute": False}) + "\n")
    (ledger / "next-steps.jsonl").write_text(json.dumps({"proposal_id": "proposal_1", "status": "ready"}) + "\n")
    svc = WorldInterfaceService(tmp_path)
    svc.record_event("test", "proposal_seen", {"proposal_id": "proposal_1"})

    console = svc.operator_console()

    assert console["status"] == "operator_console"
    assert console["pending_review"] == {"proposal_count": 1, "next_step_count": 1, "event_count": 1}
    assert console["actions_available"] == ["review", "witness", "reject", "request_more_evidence"]
    assert console["may_execute"] is False
