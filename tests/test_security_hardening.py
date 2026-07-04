from pathlib import Path

from cortex.budget import Budget
from cortex.runtime import CortexRuntime
from cortex.scl_parser import SCLAction
from cortex.tool_registry import ToolRegistry
from cortex.verifier import Verifier


def test_readonly_tool_does_not_invoke_shell_control_operators(tmp_path: Path):
    reg = ToolRegistry(workspace=str(tmp_path))
    marker = tmp_path / "pwned"

    result = reg.execute("shell.readonly", args=f"echo ok; touch {marker}")

    assert result.success
    assert not marker.exists()


def test_readonly_tool_blocks_sensitive_absolute_system_paths(tmp_path: Path):
    reg = ToolRegistry(workspace=str(tmp_path))

    result = reg.execute("shell.readonly", args="cat /etc/passwd")

    assert not result.success
    assert "system paths" in result.error


def test_runtime_snapshots_file_before_mutation(tmp_path: Path):
    target = tmp_path / "file.txt"
    target.write_text("before", encoding="utf-8")
    rt = CortexRuntime(model_fn=lambda _: "@budget → check []", workspace=str(tmp_path), store=None)
    action = SCLAction(
        anchor="@tool",
        relation="call",
        fields={"name": "shell.patch", "target": "file.txt", "risk": "write_limited"},
    )

    from cortex.rollback import RollbackManager

    rollback = RollbackManager(workspace=str(tmp_path))
    rt._snapshot_before_mutation(action, rollback, step=1)
    target.write_text("after", encoding="utf-8")

    result = rollback.rollback(str(target), reason="regression", step=2)

    assert result.success
    assert target.read_text(encoding="utf-8") == "before"


def test_budget_action_returns_snapshot(tmp_path: Path):
    rt = CortexRuntime(model_fn=lambda _: "@budget → check []", workspace=str(tmp_path), store=None)
    action = SCLAction(anchor="@budget", relation="check", fields={})

    result = rt._execute_budget(action, Budget(max_units=7))

    assert result.success
    assert '"remaining_units": 7' in result.output


def test_halt_evidence_must_link_when_verified_state_exists():
    verifier = Verifier()
    action = SCLAction(
        anchor="@halt",
        relation="answer",
        fields={"status": "complete", "confidence": 0.9, "evidence": "unrelated claim"},
    )

    result = verifier.final_check(
        "fix bug",
        {"last_verify": "passed", "verified_evidence": "pytest output: 3 passed"},
        action,
    )

    assert not result.passed
    assert "not linked" in result.reason
