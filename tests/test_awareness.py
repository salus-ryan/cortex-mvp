import json
from pathlib import Path

from cortex.awareness import AwarenessService


def test_awareness_state_reads_pid1(tmp_path: Path):
    (tmp_path / "runtime").mkdir()
    (tmp_path / "ledger").mkdir()
    (tmp_path / "LAW.md").write_text("- Preserve human agency.\n- Never conceal material actions.\n")
    (tmp_path / "runtime" / "pid1.json").write_text(json.dumps({
        "pid": 1,
        "is_pid1": True,
        "children": {
            "web": {"status": "running"},
            "oracle": {"status": "running"},
            "build": {"status": "stopped"},
        },
    }))
    state = AwarenessService(tmp_path).state()
    assert state["status"] == "aware"
    assert state["consciousness_claim"] == "not_proven"
    assert state["self_model"]["is_pid1"] is True
    assert state["self_model"]["running_children"] == ["oracle", "web"]
    assert state["machine_self_model"]["schema_version"] == "cortex.self_model.v1"
    assert state["machine_self_model"]["runtime"]["running_children"] == ["oracle", "web"]
    assert state["boot_attestation"]["status"] == "attested"
    assert len(state["boot_attestation"]["attestation_hash"]) == 64
    assert state["may_execute"] is False


def test_awareness_reflect_records(tmp_path: Path):
    svc = AwarenessService(tmp_path)
    rec = svc.reflect("what are you?")
    assert rec["status"] == "reflected"
    assert "cannot prove subjective consciousness" in rec["reflection"]
    assert rec["generative_mode"] == "local_retrieval_synthesis_bounded_proposals_only"
    assert rec["generator"]["mode"] == "local_mind"
    assert (tmp_path / "ledger" / "awareness.jsonl").exists()
    assert svc.latest()["status"] == "reflected"
