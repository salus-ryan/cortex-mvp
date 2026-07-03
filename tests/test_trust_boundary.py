import json
from pathlib import Path

from cortex.trust_boundary import TrustBoundaryService


def test_trust_boundary_records_untrusted_proposal(tmp_path: Path):
    svc = TrustBoundaryService(tmp_path)
    rec = svc.record_proposal(
        content="Suggest running tests before applying the patch.",
        proposer="rented:model",
        actor="pi",
        intent={"capability": "patch:apply"},
        witness="human",
    )
    assert rec["status"] in {"recorded", "quarantined"}
    assert rec["label"] == "untrusted_suggestion"
    assert rec["may_execute"] is False
    path = tmp_path / "ledger" / "model-proposals.jsonl"
    assert path.exists()
    row = json.loads(path.read_text().splitlines()[-1])
    assert row["proposer"] == "rented:model"


def test_trust_boundary_latest_reads_records(tmp_path: Path):
    svc = TrustBoundaryService(tmp_path)
    svc.record_proposal("Explain the diff and recommend a review.", proposer="rented:model")
    latest = svc.latest()
    assert latest["status"] == "ok"
    assert latest["records"][-1]["may_execute"] is False


def test_validate_for_action_when_required(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CORTEX_REQUIRE_PROPOSAL_IDS", "1")
    svc = TrustBoundaryService(tmp_path)
    rec = svc.record_proposal(
        "Apply the reviewed patch after tests pass.",
        intent={"path": "/patch/apply", "capability": "patch:apply"},
    )
    assert svc.validate_for_action(None, "/patch/apply", "patch:apply")["reason"] == "missing_proposal_id"
    assert svc.validate_for_action(rec["id"], "/tool/execute", "tool:execute")["reason"] == "proposal_path_mismatch"
    ok = svc.validate_for_action(rec["id"], "/patch/apply", "patch:apply")
    assert ok["allowed"] is True
    assert ok["proposal_id"] == rec["id"]


def test_next_step_returns_requirements_without_execution(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CORTEX_REQUIRE_SIGNED_INTENTS", "1")
    svc = TrustBoundaryService(tmp_path)
    rec = svc.record_proposal(
        "Apply the reviewed patch after tests pass.",
        intent={"path": "/patch/apply", "capability": "patch:apply"},
    )
    step = svc.next_step(rec["id"], "/patch/apply", "patch:apply", {"patch": "diff"})
    assert step["status"] == "ready_for_human_confirmation"
    assert step["may_execute"] is False
    assert {"auth", "proposal_id", "signed_intent", "witness", "confirmed"}.issubset(set(step["requires"]))
    assert step["intent_template"]["path"] == "/patch/apply"
    assert (tmp_path / "ledger" / "next-steps.jsonl").exists()


def test_next_step_refuses_unknown_proposal(tmp_path: Path):
    svc = TrustBoundaryService(tmp_path)
    step = svc.next_step("proposal_missing", "/patch/apply", "patch:apply")
    assert step["status"] == "refused"
    assert step["reason"] == "proposal_id_not_found"
