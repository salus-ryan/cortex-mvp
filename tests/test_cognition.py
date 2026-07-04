from pathlib import Path

from cortex.cognition import CognitionKernel


def seed_minimal_repo(root: Path) -> None:
    for path in [
        "cortex/awareness.py",
        "cortex/pid1.py",
        "cortex/memory_service.py",
        "cortex/planner.py",
        "cortex/step_function.py",
        "cortex/tool_gateway.py",
        "cortex/tool_registry.py",
        "cortex/policy.py",
        "cortex/verifier.py",
        "cortex/self_train.py",
        "cortex/trajectory_score.py",
        "cortex/web.py",
        "cortex/repo_service.py",
        "cortex/deploy_service.py",
        "cortex/immune.py",
        "cortex/witness.py",
        "cortex/trust_boundary.py",
        "Dockerfile",
        "image/live-usb/build.sh",
        "runtime/permissions.json",
        "LAW.md",
    ]:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# seed\n")


def test_cognition_status_scores_capabilities(tmp_path: Path):
    seed_minimal_repo(tmp_path)
    status = CognitionKernel(tmp_path).status()

    assert status["status"] == "cognition_status"
    assert status["claim"].startswith("AGI-ish engineering scaffold")
    assert status["may_execute"] is False
    assert status["agi_ish_score"]["score"] > 0
    assert status["agi_ish_score"]["score"] < 100
    assert status["agi_ish_score"]["possible"] == 100
    assert status["min_max"]["may_execute"] is False
    assert status["min_max"]["minimize"]
    assert status["min_max"]["maximize"]
    assert status["next_recommended_goal"]
    assert (tmp_path / "runtime" / "cognition" / "status.json").exists()


def test_cognition_tick_records_proposal_only_cycle(tmp_path: Path):
    seed_minimal_repo(tmp_path)
    result = CognitionKernel(tmp_path).tick("Improve long-horizon memory evaluation")

    assert result["status"] == "cognition_tick"
    assert result["selected_goal"] == "Improve long-horizon memory evaluation"
    assert result["may_execute"] is False
    assert result["step"]["may_execute"] is False
    assert result["memory"].get("id") or result["memory"].get("status") == "memory_refused"
    assert (tmp_path / "runtime" / "cognition" / "latest.json").exists()
    assert (tmp_path / "ledger" / "cognition.jsonl").read_text().strip()


def test_cognition_tick_bounds_empty_goal(tmp_path: Path):
    seed_minimal_repo(tmp_path)
    result = CognitionKernel(tmp_path).tick("")

    assert result["status"] == "cognition_tick"
    assert result["selected_goal"].startswith("Minimize the largest Cortex AGI-ish gap") or result[
        "selected_goal"
    ].startswith("Run a governed self-evaluation")
    assert result["may_execute"] is False
