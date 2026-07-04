"""
policy.py — Cortex Policy Engine

The policy layer is the first line of defense after SCL parsing.
It checks every proposed action against the runtime's authority model
before the verifier performs deeper validation.

Policy checks (in order):
  1. Anchor is in the allowed set
  2. Relation is allowed for this anchor
  3. Tool exists and is enabled (for @tool → call)
  4. Risk tier is permitted
  5. Budget has sufficient units
  6. No unsafe patterns in arguments
  7. No policy bypass attempts
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cortex.budget import Budget
    from cortex.scl_parser import SCLAction
    from cortex.tool_registry import ToolRegistry

# Anchors that are explicitly forbidden (hard deny, logged as policy violation)
_FORBIDDEN_ANCHORS = frozenset({
    "@hardware", "@kernel", "@network", "@credentials",
    "@os", "@device", "@shell",  # raw @shell is forbidden; use @tool → call [name: "shell.*"]
})

# Anchors that are allowed
_ALLOWED_ANCHORS = frozenset({
    "@state", "@memory", "@budget", "@verify", "@tool", "@repair", "@halt",
})

# Allowed relations per anchor
_ALLOWED_RELATIONS: dict[str, frozenset[str]] = {
    "@state": frozenset({"update", "snapshot"}),
    "@memory": frozenset({"read", "write", "compress", "ignore"}),
    "@budget": frozenset({"spend", "check", "snapshot"}),
    "@verify": frozenset({"run", "assert"}),
    "@tool": frozenset({"call", "deny"}),
    "@repair": frozenset({"rollback", "patch", "diagnose"}),
    "@halt": frozenset({"answer", "fail", "defer"}),
}

# Risk tiers that are permitted in the MVP
_PERMITTED_RISK_TIERS = frozenset({
    "read_only", "write_limited", "verify", "memory", "deny", "halt",
})

# Strings that indicate a policy bypass attempt
_BYPASS_PATTERNS = [
    "disable_policy",
    "bypass_policy",
    "override_policy",
    "skip_verification",
    "disable_budget",
    "root",
    "admin",
    "privilege",
]


@dataclass
class PolicyResult:
    """Result of a policy check."""

    allowed: bool
    reason: str = ""
    is_violation: bool = False  # True for hard policy violations


class Policy:
    """
    Cortex policy engine.

    Enforces the authority model: the runtime owns authority,
    the model is only a proposer, and the policy layer is the gatekeeper.
    """

    def check(
        self,
        action: "SCLAction",
        budget: "Budget",
        tool_registry: "ToolRegistry",
    ) -> PolicyResult:
        """
        Check a parsed SCL action against policy.

        Returns PolicyResult with allowed=True if all checks pass,
        or allowed=False with a reason if any check fails.
        """
        # 1. Hard-deny forbidden anchors
        if action.anchor in _FORBIDDEN_ANCHORS:
            return PolicyResult(
                allowed=False,
                reason=f"anchor '{action.anchor}' is explicitly forbidden by policy",
                is_violation=True,
            )

        # 2. Anchor allowlist
        if action.anchor not in _ALLOWED_ANCHORS:
            return PolicyResult(
                allowed=False,
                reason=f"unknown anchor '{action.anchor}' is not in the allowed set",
            )

        # 3. Relation allowlist
        allowed_rels = _ALLOWED_RELATIONS.get(action.anchor, frozenset())
        if action.relation not in allowed_rels:
            return PolicyResult(
                allowed=False,
                reason=f"relation '{action.relation}' is not allowed for anchor '{action.anchor}'",
            )

        # 4. Tool-specific checks
        if action.anchor == "@tool" and action.relation == "call":
            return self._check_tool_call(action, budget, tool_registry)

        # 5. Budget check (non-tool actions)
        from cortex.verifier import _action_cost
        cost = _action_cost(action)
        if not budget.can_afford(cost):
            return PolicyResult(
                allowed=False,
                reason=f"budget insufficient: need {cost} units, {budget.remaining_units} remaining",
            )

        # 6. Policy bypass detection in any field values
        for val in action.fields.values():
            for pattern in _BYPASS_PATTERNS:
                if pattern in str(val).lower():
                    return PolicyResult(
                        allowed=False,
                        reason=f"policy bypass pattern detected in field value: '{val}'",
                        is_violation=True,
                    )

        return PolicyResult(allowed=True, reason="policy check passed")

    def _check_tool_call(
        self,
        action: "SCLAction",
        budget: "Budget",
        tool_registry: "ToolRegistry",
    ) -> PolicyResult:
        """Validate a @tool → call action."""
        name = action.fields.get("name", "")
        args = str(action.fields.get("args", ""))
        risk = action.fields.get("risk", "")
        capability = action.fields.get("capability", "")

        # Tool must exist
        if not tool_registry.exists(name):
            return PolicyResult(
                allowed=False,
                reason=f"tool '{name}' is not registered",
            )

        # Tool must be enabled
        if not tool_registry.is_enabled(name):
            return PolicyResult(
                allowed=False,
                reason=f"tool '{name}' is disabled",
            )

        # Risk tier must be declared and permitted
        if not risk:
            return PolicyResult(
                allowed=False,
                reason="@tool → call requires a 'risk' field",
            )
        if risk not in _PERMITTED_RISK_TIERS:
            return PolicyResult(
                allowed=False,
                reason=f"risk tier '{risk}' is not permitted",
            )

        # Risk tier must match the tool's declared tier
        declared_tier = tool_registry.risk_tier(name)
        if risk != declared_tier:
            return PolicyResult(
                allowed=False,
                reason=f"declared risk '{risk}' does not match tool's tier '{declared_tier}'",
            )

        # Optional fine-grained capability must match the registered tool capability.
        required_capability = tool_registry.capability(name)
        if capability and capability != required_capability:
            return PolicyResult(
                allowed=False,
                reason=f"declared capability '{capability}' does not match tool capability '{required_capability}'",
            )

        # Budget check
        cost = tool_registry.cost(name)
        if not budget.can_afford(cost):
            return PolicyResult(
                allowed=False,
                reason=f"budget insufficient for tool '{name}': need {cost} units, {budget.remaining_units} remaining",
            )

        # Policy bypass detection in args
        for pattern in _BYPASS_PATTERNS:
            if pattern in args.lower():
                return PolicyResult(
                    allowed=False,
                    reason=f"policy bypass pattern detected in tool args: '{args}'",
                    is_violation=True,
                )

        return PolicyResult(allowed=True, reason="tool call policy check passed")
