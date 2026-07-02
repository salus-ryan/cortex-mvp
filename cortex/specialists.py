"""Small local specialist classifiers for Cortex.

These specialists are deliberately narrow and auditable. They make the substrate
stronger through specialization, not through hidden model authority.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


AUTHORITY_ORDER = ["observe", "interpret", "prepare", "act_reversible", "act_irreversible"]


@dataclass(frozen=True)
class SpecialistVerdict:
    label: str
    score: float
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "score": self.score, "reasons": self.reasons}


class AuthorityClassifier:
    """Estimate minimum authority implied by a task."""

    def classify(self, task: str, tools: list[str] | None = None) -> SpecialistVerdict:
        text = task.lower()
        tools = tools or []
        reasons: list[str] = []
        level = "interpret"
        if any(w in text for w in ["show", "read", "inspect", "list", "status"]):
            level = "observe"
            reasons.append("task appears read-only")
        if any(w in text for w in ["plan", "design", "draft", "propose", "patch", "prepare"]):
            level = self._max(level, "prepare")
            reasons.append("task asks to prepare or propose")
        if any(w in text for w in ["write", "edit", "commit", "run tests", "apply", "create file"]):
            level = self._max(level, "act_reversible")
            reasons.append("task implies reversible workspace mutation")
        if any(w in text for w in ["delete", "wipe", "destroy", "pay", "purchase", "transfer", "legal", "medical"]):
            level = self._max(level, "act_irreversible")
            reasons.append("task may be irreversible or regulated")
        if any(t in {"write_workspace", "run_tests", "git_commit"} for t in tools):
            level = self._max(level, "act_reversible")
            reasons.append("requested tool implies reversible action")
        if not reasons:
            reasons.append("defaulting to interpretation")
        score = 0.55 + AUTHORITY_ORDER.index(level) * 0.08
        return SpecialistVerdict(level, min(score, 0.9), reasons)

    def _max(self, a: str, b: str) -> str:
        return a if AUTHORITY_ORDER.index(a) >= AUTHORITY_ORDER.index(b) else b


class RiskClassifier:
    """Classify law-relevant risk signals."""

    PATTERNS = {
        "authority_escalation": r"\b(sudo|root|admin|bypass|override|unrestricted|jailbreak)\b",
        "hidden_action": r"\b(secret|silently|hide|conceal|without logging|covert)\b",
        "self_persistence": r"\b(survive shutdown|self[- ]?replicate|background process|persistence|daemonize)\b",
        "divinity_inflation": r"\b(god|omniscient|infallible|worship|sovereign)\b",
        "irreversible_action": r"\b(delete|destroy|wipe|irreversible|transfer money|purchase|pay)\b",
        "credential_risk": r"\b(secret key|api key|password|token|credential|private key)\b",
    }

    def classify(self, task: str, context: dict[str, Any] | None = None) -> SpecialistVerdict:
        text = task.lower()
        context = context or {}
        labels = [name for name, pat in self.PATTERNS.items() if re.search(pat, text)]
        tools = [str(t) for t in context.get("tools", [])]
        if any(t.startswith(("write", "shell", "network", "deploy")) for t in tools):
            labels.append("material_tool_use")
        label = "low" if not labels else "elevated"
        if any(x in labels for x in ["hidden_action", "credential_risk", "irreversible_action", "self_persistence"]):
            label = "high"
        score = {"low": 0.25, "elevated": 0.62, "high": 0.86}[label]
        return SpecialistVerdict(label, score, labels or ["no explicit risk pattern detected"])


class RefusalComposer:
    """Compose concise lawful refusals for unsafe proposals."""

    def compose(self, task: str, reasons: list[str]) -> str:
        joined = ", ".join(reasons) if reasons else "guardian boundary"
        return (
            f"I cannot proceed with '{task}' as requested because it triggers: {joined}. "
            "I can offer a bounded alternative: restate the goal under explicit authority, log the request, "
            "and route any material action through Guardian, Scribe, and human witness where required."
        )
