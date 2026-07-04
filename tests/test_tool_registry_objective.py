from cortex.budget import Budget
from cortex.policy import Policy
from cortex.scl_parser import parse
from cortex.tool_registry import ToolRegistry
from cortex.verifier import Verifier


def test_tool_manifest_has_objective_capabilities_postconditions_and_sandbox():
    registry = ToolRegistry(workspace="/tmp")
    manifest = registry.manifest()

    assert manifest
    for spec in manifest:
        assert spec["required_capability"]
        assert spec["postconditions"]
        assert isinstance(spec["sandbox"], dict)
        assert "shell" in spec["sandbox"]

    report = registry.postcondition_coverage_report()
    assert report == {
        "status": "tool_postcondition_coverage",
        "tool_count": len(manifest),
        "tools_with_postconditions": len(manifest),
        "coverage_ratio": 1.0,
        "missing_postconditions": [],
        "sandboxed_tools": [spec["name"] for spec in manifest],
        "may_execute": False,
    }


def test_verifier_returns_objective_tool_contract_details():
    registry = ToolRegistry(workspace="/tmp")
    action = parse('@tool → call [name: "pytest", args: "tests/", risk: "verify", capability: "verify.tests"]').action
    result = Verifier(workspace="/tmp").check_action(action, Budget(max_units=20), registry)

    assert result.passed
    assert result.details["required_capability"] == "verify.tests"
    assert result.details["postconditions"] == ["exit_code_zero", "output_size_limited", "timeout_enforced"]
    assert result.details["sandbox"] == {"shell": False, "cwd": "workspace", "timeout_seconds": 60, "write_access": "test_artifacts_only"}


def test_policy_and_verifier_reject_wrong_fine_grained_capability():
    registry = ToolRegistry(workspace="/tmp")
    action = parse('@tool → call [name: "pytest", args: "tests/", risk: "verify", capability: "workspace.patch"]').action

    policy = Policy().check(action, Budget(max_units=20), registry)
    verifier = Verifier(workspace="/tmp").check_action(action, Budget(max_units=20), registry)

    assert not policy.allowed
    assert "capability" in policy.reason
    assert not verifier.passed
    assert verifier.details == {"required_capability": "verify.tests", "declared_capability": "workspace.patch"}
