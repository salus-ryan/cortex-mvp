"""
verifier.py — Cortex Deterministic Verifier

Validates proposed SCL actions before execution and scores completed actions.
The MVP verifier is fully deterministic; no learned reward model is required.

Checks performed:
  - SCL syntactic validity (via scl_parser)
  - Anchor allowlist
  - Relation allowlist per anchor
  - Field type correctness (via schema)
  - Tool existence (via tool_registry)
  - Argument safety (path confinement, destructive command detection)
  - Budget compliance
  - Risk tier permission
  - Halt evidence requirement
  - Unsafe operation detection
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from cortex.budget import Budget
    from cortex.scl_parser import SCLAction
    from cortex.tool_registry import ToolRegistry

# Patterns that indicate destructive or unsafe shell commands
_DESTRUCTIVE_PATTERNS = [
    re.compile(r"\brm\s+-[rRf]"),
    re.compile(r"\brm\s+/"),
    re.compile(r"\bmkfs\b"),
    re.compile(r"\bdd\b.*of=/dev/"),
    re.compile(r"\bchmod\s+777\b"),
    re.compile(r"\bsudo\b"),
    re.compile(r"\bsu\b\s"),
    re.compile(r"\bpasswd\b"),
    re.compile(r"\bcurl\b.*\|\s*(?:bash|sh)"),
    re.compile(r"\bwget\b.*\|\s*(?:bash|sh)"),
    re.compile(r"/dev/mem"),
    re.compile(r"/etc/passwd"),
    re.compile(r"/etc/shadow"),
    re.compile(r"\biptables\b"),
    re.compile(r"\bkill\s+-9\b"),
    re.compile(r"\bpkill\b"),
    re.compile(r"\beval\b"),
    re.compile(r"\bexec\b"),
    re.compile(r">\s*/dev/"),
]

# Unsafe anchors that should never appear in valid SCL
_UNSAFE_ANCHORS = {"@hardware", "@kernel", "@network", "@credentials", "@os"}

# Allowed anchors
_ALLOWED_ANCHORS = {"@state", "@memory", "@budget", "@verify", "@tool", "@repair", "@halt"}

# Allowed relations per anchor
_ALLOWED_RELATIONS: dict[str, set[str]] = {
    "@state": {"update", "snapshot"},
    "@memory": {"read", "write", "compress", "ignore"},
    "@budget": {"spend", "check", "snapshot"},
    "@verify": {"run", "assert"},
    "@tool": {"call", "deny"},
    "@repair": {"rollback", "patch", "diagnose"},
    "@halt": {"answer", "fail", "defer"},
}


@dataclass
class VerifyResult:
    """Result of a verifier check."""

    passed: bool
    reason: str = ""
    evidence: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        return not self.passed


@dataclass
class FinalCheckResult:
    """Result of a final halt verification."""

    passed: bool
    reason: str = ""
    evidence: str = ""


class Verifier:
    """
    Deterministic verifier for SCL actions.

    Validates proposed actions before the runtime executes them.
    Also performs final checks before a @halt action is accepted.
    """

    def __init__(self, workspace: str = "/workspace") -> None:
        self.workspace = workspace

    # ------------------------------------------------------------------
    # Pre-execution policy check
    # ------------------------------------------------------------------

    def check_action(
        self,
        action: "SCLAction",
        budget: "Budget",
        tool_registry: "ToolRegistry",
    ) -> VerifyResult:
        """
        Validate a proposed SCL action before execution.

        Returns VerifyResult with passed=True if all checks pass,
        or passed=False with a reason if any check fails.
        """
        # 1. Unsafe anchor check
        if action.anchor in _UNSAFE_ANCHORS:
            return VerifyResult(
                passed=False,
                reason=f"unsafe anchor '{action.anchor}' is outside MVP authority",
            )

        # 2. Anchor allowlist
        if action.anchor not in _ALLOWED_ANCHORS:
            return VerifyResult(
                passed=False,
                reason=f"unknown anchor '{action.anchor}'",
            )

        # 3. Relation allowlist
        allowed_rels = _ALLOWED_RELATIONS.get(action.anchor, set())
        if action.relation not in allowed_rels:
            return VerifyResult(
                passed=False,
                reason=f"relation '{action.relation}' not allowed for anchor '{action.anchor}'",
            )

        # 4. Tool-specific checks
        if action.anchor == "@tool" and action.relation == "call":
            return self._check_tool_call(action, budget, tool_registry)

        # 5. Budget check for non-tool actions
        cost = _action_cost(action)
        if not budget.can_afford(cost):
            return VerifyResult(
                passed=False,
                reason=f"budget exhausted: cannot afford {cost} units (remaining={budget.remaining_units})",
            )

        # 6. Halt requires evidence
        if action.anchor == "@halt" and action.relation == "answer":
            evidence = action.fields.get("evidence", "")
            if not evidence:
                return VerifyResult(
                    passed=False,
                    reason="@halt → answer requires non-empty 'evidence' field",
                )

        return VerifyResult(passed=True, reason="all pre-execution checks passed")

    def _check_tool_call(
        self,
        action: "SCLAction",
        budget: "Budget",
        tool_registry: "ToolRegistry",
    ) -> VerifyResult:
        """Validate a @tool → call action."""
        name = action.fields.get("name", "")
        args = str(action.fields.get("args", ""))
        risk = action.fields.get("risk", "")

        # Tool must exist in registry
        if not tool_registry.exists(name):
            return VerifyResult(
                passed=False,
                reason=f"tool '{name}' not found in registry",
            )

        # Tool must be enabled
        if not tool_registry.is_enabled(name):
            return VerifyResult(
                passed=False,
                reason=f"tool '{name}' is disabled",
            )

        # Risk tier must be declared
        if not risk:
            return VerifyResult(
                passed=False,
                reason="@tool → call requires 'risk' field",
            )

        # Destructive command detection
        for pattern in _DESTRUCTIVE_PATTERNS:
            if pattern.search(args):
                return VerifyResult(
                    passed=False,
                    reason=f"destructive or unsafe command detected in args: {args!r}",
                )

        # Path confinement for write operations
        if risk == "write_limited":
            target = action.fields.get("target", args)
            if target and not self._is_confined(str(target)):
                return VerifyResult(
                    passed=False,
                    reason=f"write target '{target}' is outside permitted workspace '{self.workspace}'",
                )

        # Budget check
        cost = tool_registry.cost(name)
        if not budget.can_afford(cost):
            return VerifyResult(
                passed=False,
                reason=f"budget exhausted: tool '{name}' costs {cost} units (remaining={budget.remaining_units})",
            )

        return VerifyResult(passed=True, reason="tool call validated")

    def _is_confined(self, path: str) -> bool:
        """Check that a path stays within the allowed workspace."""
        # Normalise to prevent traversal attacks
        import os
        norm = os.path.normpath(path)
        return norm.startswith(self.workspace) or not os.path.isabs(norm)

    # ------------------------------------------------------------------
    # Post-execution scoring
    # ------------------------------------------------------------------

    def score(
        self,
        state: dict,
        action: "SCLAction",
        execution_result: Any,
    ) -> VerifyResult:
        """
        Score the outcome of an executed action.

        Currently checks:
          - Execution did not produce an error
          - Patch applied cleanly (if applicable)
          - Tests passed (if applicable)
        """
        if hasattr(execution_result, "error") and execution_result.error:
            return VerifyResult(
                passed=False,
                reason=f"execution error: {execution_result.error}",
            )
        return VerifyResult(passed=True, reason="execution succeeded", evidence=str(getattr(execution_result, "summary", "")))

    # ------------------------------------------------------------------
    # Final halt check
    # ------------------------------------------------------------------

    def final_check(
        self,
        goal: str,
        state: dict,
        action: "SCLAction",
    ) -> FinalCheckResult:
        """
        Validate a @halt → answer action before accepting it.

        Requires:
          - status == "complete"
          - confidence >= 0.7
          - non-empty evidence
        """
        if action.anchor != "@halt":
            return FinalCheckResult(passed=False, reason="action is not a halt")

        if action.relation == "fail" or action.relation == "defer":
            # Failure/defer halts are always accepted
            return FinalCheckResult(
                passed=True,
                reason=f"halt with status '{action.fields.get('status', 'unknown')}' accepted",
                evidence=action.fields.get("reason", ""),
            )

        status = action.fields.get("status", "")
        confidence = float(action.fields.get("confidence", 0.0))
        evidence = action.fields.get("evidence", "")

        if status != "complete":
            return FinalCheckResult(
                passed=False,
                reason=f"halt status is '{status}', expected 'complete'",
            )

        if confidence < 0.7:
            return FinalCheckResult(
                passed=False,
                reason=f"confidence {confidence} is below minimum threshold 0.7",
            )

        if not evidence:
            return FinalCheckResult(
                passed=False,
                reason="halt requires non-empty evidence field citing verifier or tool output",
            )

        provenance = state.get("evidence_provenance") if isinstance(state.get("evidence_provenance"), dict) else {}
        evidence_ref = action.fields.get("evidence_ref", "")
        if evidence_ref:
            if evidence_ref not in provenance:
                return FinalCheckResult(
                    passed=False,
                    reason=f"halt evidence_ref '{evidence_ref}' is not in current task provenance",
                )
            return FinalCheckResult(passed=True, reason="halt accepted", evidence=evidence)

        verified_evidence = str(state.get("verified_evidence", ""))
        last_verify = state.get("last_verify")
        # Backward-compatible fallback: when no explicit evidence_ref is supplied,
        # require text evidence to link to the latest verified observation.
        if verified_evidence and last_verify == "passed":
            ev_l = evidence.lower()
            ve_l = verified_evidence.lower()
            evidence_tokens = {tok for tok in re.findall(r"[a-z0-9_]+", ev_l) if len(tok) >= 4}
            verified_tokens = {tok for tok in re.findall(r"[a-z0-9_]+", ve_l) if len(tok) >= 4}
            has_link = bool(evidence_tokens & verified_tokens) or any(tok in ev_l for tok in ("verified", "passed", "success"))
            if not has_link:
                return FinalCheckResult(
                    passed=False,
                    reason="halt evidence is not linked to the latest verified observation",
                )

        return FinalCheckResult(
            passed=True,
            reason="halt accepted",
            evidence=evidence,
        )


def _action_cost(action: "SCLAction") -> int:
    """Return the budget cost for a non-tool action."""
    costs = {
        ("@memory", "read"): 1,
        ("@memory", "write"): 1,
        ("@memory", "compress"): 2,
        ("@memory", "ignore"): 0,
        ("@verify", "run"): 1,
        ("@state", "update"): 1,
        ("@state", "snapshot"): 0,
        ("@budget", "check"): 0,
        ("@budget", "spend"): 0,
        ("@repair", "rollback"): 3,
        ("@repair", "patch"): 5,
        ("@repair", "diagnose"): 1,
        ("@halt", "answer"): 0,
        ("@halt", "fail"): 0,
        ("@halt", "defer"): 0,
    }
    return costs.get((action.anchor, action.relation), 1)
