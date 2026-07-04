from pathlib import Path

from cortex.runtime import CortexRuntime
from cortex.scl_parser import SCLAction
from cortex.tool_registry import ExecutionResult
from cortex.verifier import Verifier


def test_runtime_creates_evidence_provenance_for_verified_tool(tmp_path: Path):
    rt = CortexRuntime(model_fn=lambda _: "@budget → check []", workspace=str(tmp_path), store=None)
    action = SCLAction(anchor="@tool", relation="call", fields={"name": "shell.readonly"}, raw='@tool → call [name: "shell.readonly"]')
    result = ExecutionResult(tool="shell.readonly", success=True, output="read ok")

    state = rt._transition_state({}, action, result, object())

    ref = state["latest_evidence_ref"]
    assert ref.startswith("prov_")
    assert ref in state["evidence_provenance"]
    assert state["evidence_provenance"][ref]["summary"] == "read ok"


def test_halt_accepts_current_evidence_ref():
    verifier = Verifier()
    action = SCLAction(
        anchor="@halt",
        relation="answer",
        fields={"status": "complete", "confidence": 0.9, "evidence": "verified by ref", "evidence_ref": "prov_abc"},
    )

    result = verifier.final_check("goal", {"evidence_provenance": {"prov_abc": {"summary": "ok"}}}, action)

    assert result.passed


def test_halt_rejects_forged_evidence_ref():
    verifier = Verifier()
    action = SCLAction(
        anchor="@halt",
        relation="answer",
        fields={"status": "complete", "confidence": 0.9, "evidence": "verified by ref", "evidence_ref": "prov_fake"},
    )

    result = verifier.final_check("goal", {"evidence_provenance": {"prov_real": {"summary": "ok"}}}, action)

    assert not result.passed
    assert "not in current task provenance" in result.reason
