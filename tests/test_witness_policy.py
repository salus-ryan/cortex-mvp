from pathlib import Path

from cortex.witness import WitnessService


def test_witness_risk_policy_is_objective(tmp_path: Path):
    policy = WitnessService(tmp_path).risk_policy()

    assert policy["status"] == "witness_risk_policy"
    assert policy["tiers"]["act_irreversible"] == {
        "witnesses_required": 2,
        "signature_required": True,
        "human_confirmation": True,
    }
    assert policy["may_execute"] is False


def test_witness_policy_check_counts_scope_and_signature(tmp_path: Path):
    svc = WitnessService(tmp_path)
    svc.witness("a", "reviewed", "deploy", signature="sig")
    svc.witness("b", "reviewed", "deploy")

    allowed = svc.check_risk_policy("act_irreversible", "deploy")
    blocked = svc.check_risk_policy("act_irreversible", "other")

    assert allowed["allowed"] is True
    assert blocked["allowed"] is False
    assert {c["name"] for c in allowed["checks"]} == {"witness_count", "signature"}
    assert allowed["may_execute"] is False
