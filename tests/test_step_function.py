from pathlib import Path

from cortex.step_function import CortexStepFunction


def test_step_function_runs_one_bounded_cycle(tmp_path: Path):
    (tmp_path / "LAW.md").write_text("Preserve human agency. Keep a ledger.")
    stepper = CortexStepFunction(tmp_path)
    result = stepper.step("Plan a safe memory improvement", "interpret")
    assert result["status"] == "stepped"
    assert result["may_execute"] is False
    assert result["proposal_id"].startswith("proposal_")
    assert result["observation"]["may_execute"] is False
    assert (tmp_path / "ledger" / "steps.jsonl").exists()
    assert (tmp_path / "ledger" / "model-proposals.jsonl").exists()
    latest = stepper.latest()
    assert latest["goal"] == "Plan a safe memory improvement"


def test_step_function_with_material_context_returns_next_step(tmp_path: Path):
    (tmp_path / "LAW.md").write_text("Preserve human agency. Keep a ledger.")
    stepper = CortexStepFunction(tmp_path)
    result = stepper.step(
        "Prepare a factual memory write for human confirmation",
        "interpret",
        {"path": "/memory/write", "capability": "memory:write", "payload": {"type": "factual"}},
    )
    assert result["status"] == "stepped"
    assert result["next_step"]["status"] == "ready_for_human_confirmation"
    assert result["requires_human"] is True
    assert result["may_execute"] is False
    assert (tmp_path / "ledger" / "next-steps.jsonl").exists()


def test_step_function_refuses_empty_goal(tmp_path: Path):
    result = CortexStepFunction(tmp_path).step("")
    assert result["status"] == "refused"
    assert result["may_execute"] is False
