from pathlib import Path

from cortex.loop import CortexLoop


def test_loop_runs_bounded_steps(tmp_path: Path):
    (tmp_path / "LAW.md").write_text("Preserve human agency. Keep a ledger.")
    result = CortexLoop(tmp_path).run("Improve the project safely", max_steps=2)
    assert result["status"] == "looped"
    assert 1 <= result["steps_run"] <= 2
    assert result["may_execute"] is False
    assert result["stop_reason"] in {"max_steps_reached", "requires_human_confirmation", "no_useful_next_goal", "repeated_goal", "immune_watch", "immune_inflamed", "immune_quarantine"}
    assert (tmp_path / "ledger" / "loops.jsonl").exists()
    assert (tmp_path / "runtime" / "loop" / "latest.json").exists()


def test_loop_hard_caps_max_steps(tmp_path: Path):
    (tmp_path / "LAW.md").write_text("Preserve human agency. Keep a ledger.")
    result = CortexLoop(tmp_path).run("Reflect safely", max_steps=99)
    assert result["max_steps"] == CortexLoop.HARD_MAX_STEPS
    assert result["steps_run"] <= CortexLoop.HARD_MAX_STEPS
    assert result["may_execute"] is False


def test_loop_refuses_empty_goal(tmp_path: Path):
    result = CortexLoop(tmp_path).run("")
    assert result["status"] == "refused"
    assert result["may_execute"] is False
