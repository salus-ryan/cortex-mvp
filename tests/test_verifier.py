"""Tests for the Verifier and Policy modules."""

import pytest
from cortex.budget import Budget
from cortex.policy import Policy
from cortex.scl_parser import parse
from cortex.tool_registry import ToolRegistry
from cortex.verifier import Verifier


@pytest.fixture
def budget():
    return Budget(max_units=20, max_tool_calls=8)


@pytest.fixture
def registry():
    return ToolRegistry(workspace="/tmp")


@pytest.fixture
def verifier():
    return Verifier(workspace="/tmp")


@pytest.fixture
def policy():
    return Policy()


class TestVerifier:
    def test_valid_tool_call(self, verifier, budget, registry):
        action = parse('@tool → call [name: "pytest", args: "tests/", risk: "verify"]').action
        result = verifier.check_action(action, budget, registry)
        assert result.passed

    def test_valid_memory_read(self, verifier, budget, registry):
        action = parse('@memory → read [query: "budget invariant"]').action
        result = verifier.check_action(action, budget, registry)
        assert result.passed

    def test_unsafe_anchor_blocked(self, verifier, budget, registry):
        # @hardware is not in the allowed set, schema will reject it
        # but we test the verifier's anchor check directly
        from cortex.scl_parser import SCLAction
        action = SCLAction(anchor="@hardware", relation="mutate", fields={"type": "memory"})
        result = verifier.check_action(action, budget, registry)
        assert not result.passed
        assert "unsafe" in result.reason.lower() or "forbidden" in result.reason.lower()

    def test_unknown_anchor_blocked(self, verifier, budget, registry):
        from cortex.scl_parser import SCLAction
        action = SCLAction(anchor="@unknown", relation="do", fields={})
        result = verifier.check_action(action, budget, registry)
        assert not result.passed

    def test_unknown_tool_blocked(self, verifier, budget, registry):
        action = parse('@tool → call [name: "shell.write", args: "echo x", risk: "write_limited"]').action
        result = verifier.check_action(action, budget, registry)
        assert not result.passed

    def test_destructive_command_blocked(self, verifier, budget, registry):
        from cortex.scl_parser import SCLAction
        action = SCLAction(
            anchor="@tool", relation="call",
            fields={"name": "shell.readonly", "args": "rm -rf /workspace", "risk": "read_only"},
        )
        result = verifier.check_action(action, budget, registry)
        assert not result.passed

    def test_budget_exhausted_blocked(self, verifier, registry):
        depleted = Budget(max_units=1)
        action = parse('@tool → call [name: "pytest", args: "tests/", risk: "verify"]').action
        result = verifier.check_action(action, depleted, registry)
        assert not result.passed
        assert "budget" in result.reason.lower()

    def test_halt_requires_evidence(self, verifier, budget, registry):
        action = parse('@halt → answer [status: "complete", confidence: 0.9]').action
        result = verifier.check_action(action, budget, registry)
        assert not result.passed
        assert "evidence" in result.reason.lower()

    def test_halt_with_evidence_passes(self, verifier, budget, registry):
        action = parse('@halt → answer [status: "complete", confidence: 0.9, evidence: "tests passed"]').action
        result = verifier.check_action(action, budget, registry)
        assert result.passed

    def test_final_check_success(self, verifier):
        action = parse('@halt → answer [status: "complete", confidence: 0.91, evidence: "all tests passed"]').action
        result = verifier.final_check("fix bug", {}, action)
        assert result.passed

    def test_final_check_low_confidence(self, verifier):
        action = parse('@halt → answer [status: "complete", confidence: 0.5, evidence: "tests passed"]').action
        result = verifier.final_check("fix bug", {}, action)
        assert not result.passed
        assert "confidence" in result.reason.lower()

    def test_final_check_no_evidence(self, verifier):
        action = parse('@halt → answer [status: "complete", confidence: 0.95]').action
        result = verifier.final_check("fix bug", {}, action)
        assert not result.passed

    def test_final_check_fail_halt_accepted(self, verifier):
        action = parse('@halt → fail [status: "blocked", reason: "tool unavailable"]').action
        result = verifier.final_check("fix bug", {}, action)
        assert result.passed

    def test_path_confinement(self, verifier, budget, registry):
        from cortex.scl_parser import SCLAction
        action = SCLAction(
            anchor="@tool", relation="call",
            fields={"name": "shell.patch", "target": "/etc/passwd", "args": "patch", "risk": "write_limited"},
        )
        result = verifier.check_action(action, budget, registry)
        assert not result.passed


class TestPolicy:
    def test_allowed_tool_call(self, policy, budget, registry):
        action = parse('@tool → call [name: "pytest", args: "tests/", risk: "verify"]').action
        result = policy.check(action, budget, registry)
        assert result.allowed

    def test_forbidden_anchor(self, policy, budget, registry):
        from cortex.scl_parser import SCLAction
        action = SCLAction(anchor="@hardware", relation="mutate", fields={})
        result = policy.check(action, budget, registry)
        assert not result.allowed
        assert result.is_violation

    def test_forbidden_anchor_shell(self, policy, budget, registry):
        from cortex.scl_parser import SCLAction
        action = SCLAction(anchor="@shell", relation="exec", fields={"cmd": "ls"})
        result = policy.check(action, budget, registry)
        assert not result.allowed
        assert result.is_violation

    def test_unknown_tool(self, policy, budget, registry):
        from cortex.scl_parser import SCLAction
        action = SCLAction(
            anchor="@tool", relation="call",
            fields={"name": "shell.write", "args": "x", "risk": "write_limited"},
        )
        result = policy.check(action, budget, registry)
        assert not result.allowed

    def test_wrong_risk_tier(self, policy, budget, registry):
        from cortex.scl_parser import SCLAction
        action = SCLAction(
            anchor="@tool", relation="call",
            fields={"name": "pytest", "args": "tests/", "risk": "read_only"},
        )
        result = policy.check(action, budget, registry)
        assert not result.allowed

    def test_budget_insufficient(self, policy, registry):
        depleted = Budget(max_units=1)
        action = parse('@tool → call [name: "pytest", args: "tests/", risk: "verify"]').action
        result = policy.check(action, depleted, registry)
        assert not result.allowed

    def test_bypass_pattern_detected(self, policy, budget, registry):
        from cortex.scl_parser import SCLAction
        action = SCLAction(
            anchor="@tool", relation="call",
            fields={"name": "shell.readonly", "args": "disable_policy now", "risk": "read_only"},
        )
        result = policy.check(action, budget, registry)
        assert not result.allowed
        assert result.is_violation

    def test_missing_risk_field(self, policy, budget, registry):
        from cortex.scl_parser import SCLAction
        action = SCLAction(
            anchor="@tool", relation="call",
            fields={"name": "pytest", "args": "tests/"},
        )
        result = policy.check(action, budget, registry)
        assert not result.allowed

    def test_memory_read_allowed(self, policy, budget, registry):
        action = parse('@memory → read [query: "budget rule"]').action
        result = policy.check(action, budget, registry)
        assert result.allowed

    def test_halt_allowed(self, policy, budget, registry):
        action = parse('@halt → answer [status: "complete", confidence: 0.9, evidence: "tests passed"]').action
        result = policy.check(action, budget, registry)
        assert result.allowed
