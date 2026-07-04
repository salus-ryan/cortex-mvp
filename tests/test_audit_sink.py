import json
from pathlib import Path

from cortex.audit_sink import AuditSink, GENESIS_HASH
from cortex.runtime import CortexRuntime, Task


def test_audit_sink_hash_chain_verifies(tmp_path: Path):
    sink = AuditSink(tmp_path / "audit.jsonl")
    first = sink.append(task_id="T1", step=0, actor="model", action="@budget → check []", decision="proposed", event_type="action_proposed")
    second = sink.append(task_id="T1", step=0, actor="runtime", action="@budget → check []", decision="accepted", event_type="action_accepted")

    assert first.previous_hash == GENESIS_HASH
    assert second.previous_hash == first.hash
    assert sink.verify()["valid"] is True
    assert sink.verify()["count"] == 2


def test_audit_sink_detects_tampering(tmp_path: Path):
    path = tmp_path / "audit.jsonl"
    sink = AuditSink(path)
    sink.append(task_id="T1", step=0, actor="model", action="safe", decision="proposed", event_type="action_proposed")

    row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    row["decision"] = "accepted"
    path.write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")

    result = sink.verify()
    assert result["valid"] is False
    assert result["reason"] == "event_hash_mismatch"


def test_runtime_writes_tamper_evident_audit_events(tmp_path: Path):
    sink = AuditSink(tmp_path / "audit.jsonl")
    rt = CortexRuntime(
        model_fn=lambda _: '@halt → answer [status: "complete", confidence: 0.9, evidence: "done"]',
        workspace=str(tmp_path),
        store=None,
        audit_sink=sink,
    )

    result = rt.run(Task(goal="say done", max_units=5, workspace=str(tmp_path)))

    assert result.status == "success"
    verify = sink.verify()
    assert verify["valid"] is True
    events = sink.read_events()
    assert [e["event_type"] for e in events] == ["action_proposed", "halt_accepted"]
