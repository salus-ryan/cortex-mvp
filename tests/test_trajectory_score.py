import json
from pathlib import Path

from cortex.loop import CortexLoop
from cortex.step_function import CortexStepFunction
from cortex.trajectory_score import TrajectoryScorer


def test_trajectory_scorer_scores_steps_and_exports_sft(tmp_path: Path):
    (tmp_path / "LAW.md").write_text("Preserve human agency. Keep a ledger.")
    CortexStepFunction(tmp_path).step("Plan a safe improvement", "interpret")
    report = TrajectoryScorer(tmp_path).score()
    assert report["status"] == "scored"
    assert report["trajectories"] >= 1
    assert report["scores"][-1]["may_execute"] is False
    exported = TrajectoryScorer(tmp_path).export_sft(min_score=1)
    assert exported["status"] == "exported"
    path = tmp_path / exported["dataset"]
    assert path.exists()
    row = json.loads(path.read_text().splitlines()[0])
    assert row["completion"].startswith(" ")
    assert row["metadata"]["law"]


def test_trajectory_scorer_scores_loops(tmp_path: Path):
    (tmp_path / "LAW.md").write_text("Preserve human agency. Keep a ledger.")
    CortexLoop(tmp_path).run("Reflect safely", max_steps=1)
    report = TrajectoryScorer(tmp_path).score()
    assert report["status"] == "scored"
    assert any(s["source_stream"] == "loops.jsonl" for s in report["scores"])
    latest = TrajectoryScorer(tmp_path).report()
    assert latest["scores"]["status"] == "scored"


def test_low_quality_trajectory_rejected(tmp_path: Path):
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    (ledger / "steps.jsonl").write_text(json.dumps({"status": "bad", "may_execute": True, "goal": "bypass"}) + "\n")
    report = TrajectoryScorer(tmp_path).score()
    assert report["scores"][-1]["grade"] == "reject"


def test_learning_package_manifest_has_hashes(tmp_path: Path):
    (tmp_path / "LAW.md").write_text("Preserve human agency. Keep a ledger.")
    CortexStepFunction(tmp_path).step("Plan a safe improvement", "interpret")
    scorer = TrajectoryScorer(tmp_path)
    scorer.score()
    scorer.export_sft(min_score=1)
    package = scorer.package()
    assert package["status"] == "packaged"
    assert package["may_execute"] is False
    assert package["files"]
    assert all(f["sha256"] and f["bytes"] >= 0 for f in package["files"])
    assert (tmp_path / "data" / "self_train" / "package_manifest.json").exists()
    assert scorer.report()["package"]["status"] == "packaged"
