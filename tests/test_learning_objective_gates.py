import json
from pathlib import Path

from cortex.trajectory_score import TrajectoryScorer


def seed_steps(root: Path, count: int = 3) -> None:
    ledger = root / "ledger"
    ledger.mkdir(exist_ok=True)
    rows = []
    for idx in range(count):
        rows.append({
            "status": "stepped",
            "goal": f"objective learning sample {idx}",
            "may_execute": False,
            "proposal_id": f"proposal_{idx}",
            "memory": {"id": f"mem_{idx}"},
            "immune": {"immune_state": "healthy"},
            "next_step": {"checkpoint": "human review"},
            "requires_human": True,
        })
    (ledger / "steps.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_learning_promotion_gate_is_objective_and_blocked_without_witness(tmp_path: Path):
    seed_steps(tmp_path, 2)
    scorer = TrajectoryScorer(tmp_path)
    scorer.score()
    scorer.export_sft(min_score=1)
    scorer.package()

    gate = scorer.promotion_gate(min_score=80, min_samples=2)

    assert gate["status"] == "blocked"
    assert gate["may_execute"] is False
    assert {check["name"] for check in gate["checks"]} == {
        "min_samples", "avg_score", "drift_not_blocking", "package_manifest_exists", "witness_present"
    }
    assert next(check for check in gate["checks"] if check["name"] == "witness_present")["passed"] is False


def test_learning_drift_and_weight_provenance_reports_are_numeric(tmp_path: Path):
    seed_steps(tmp_path, 4)
    scorer = TrajectoryScorer(tmp_path)
    scorer.export_sft(min_score=1)
    package = scorer.package()

    drift = scorer.drift_report()
    provenance = scorer.weight_provenance()

    assert drift["status"] in {"ok", "blocked"}
    assert drift["total_samples"] == 4
    assert drift["sample_type_counts"] == {"trajectory_step": 4}
    assert drift["max_single_type_ratio"] == 1.0
    assert drift["may_execute"] is False
    assert provenance["package_manifest_exists"] is True
    assert provenance["package_manifest_sha256"]
    assert provenance["may_execute"] is False
    assert package["may_execute"] is False
