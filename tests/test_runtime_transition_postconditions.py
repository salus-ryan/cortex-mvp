from pathlib import Path

from cortex.audit_sink import AuditSink
from cortex.runtime import CortexRuntime, Task


def test_runtime_audits_transition_metadata_for_accepted_action(tmp_path: Path):
    calls = iter([
        '@budget → check []',
        '@halt → answer [status: "complete", confidence: 0.9, evidence: "checked"]',
    ])
    sink = AuditSink(tmp_path / "audit.jsonl")
    rt = CortexRuntime(model_fn=lambda _: next(calls), workspace=str(tmp_path), store=None, audit_sink=sink)

    result = rt.run(Task(goal="check budget then halt", workspace=str(tmp_path), max_units=10))

    assert result.status == "success"
    accepted = [e for e in sink.read_events() if e["event_type"] == "action_accepted"]
    assert accepted
    assert accepted[0]["data"]["transition_phase"] == "budget"
    assert accepted[0]["data"]["terminal"] is False
    assert sink.verify()["valid"] is True
