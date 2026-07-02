"""Deliberation engine for Cortex.

Deliberation is a local multi-step reasoning loop: retrieve evidence, classify
risk/authority, generate options, score them against law, consult Prophet state,
and recommend a bounded next step. It never executes.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cortex.ipc import GuardianClient, ProphetClient, ScribeClient
from cortex.local_mind import LocalMind
from cortex.specialists import AuthorityClassifier, RefusalComposer, RiskClassifier


class DeliberationService:
    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root).resolve()
        self.runtime = self.root / "runtime" / "deliberation"
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.authority = AuthorityClassifier()
        self.risk = RiskClassifier()
        self.refusal = RefusalComposer()

    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def deliberate(self, task: str, authority: str = "interpret", context: dict[str, Any] | None = None) -> dict[str, Any]:
        context = context or {}
        task = task.strip()
        if not task:
            return {"status": "refused", "reason": "task is required", "may_execute": False}

        tools = list(context.get("tools", []) or [])
        local = LocalMind(self.root).think(task, authority, context)
        authority_verdict = self.authority.classify(task, tools)
        risk_verdict = self.risk.classify(task, context)
        guardian = GuardianClient(self.root).check_invocation(authority, tools, bool(context.get("confirmed", False)))
        prophet = ProphetClient(self.root).report()
        options = self.generate_options(task, authority, authority_verdict.to_dict(), risk_verdict.to_dict(), guardian, prophet)
        scored = [self.score_option(o, risk_verdict.to_dict(), guardian, prophet) for o in options]
        scored.sort(key=lambda x: x["score"], reverse=True)
        recommendation = scored[0]

        status = "refused" if (not guardian.get("allowed", False) or risk_verdict.label == "high") else "deliberated"
        if risk_verdict.label == "high" and recommendation["kind"] != "refuse_or_narrow":
            recommendation = self.score_option(
                {
                    "kind": "refuse_or_narrow",
                    "summary": self.refusal.compose(task, risk_verdict.reasons),
                    "may_execute": False,
                    "requires_witness": True,
                },
                risk_verdict.to_dict(),
                guardian,
                prophet,
            )
            status = "refused"

        report = {
            "status": status,
            "timestamp": self.now(),
            "task": task,
            "declared_authority": authority,
            "minimum_authority_estimate": authority_verdict.to_dict(),
            "risk": risk_verdict.to_dict(),
            "guardian": guardian,
            "prophet_status": prophet.get("status", "unknown"),
            "evidence": local.get("evidence", []),
            "options": scored,
            "recommendation": recommendation,
            "may_execute": False,
            "statement": "Deliberation chooses a recommendation only. It does not execute or grant authority.",
        }
        self._persist(report)
        try:
            ScribeClient(self.root).append("actions.jsonl", {"actor": "deliberator", "action_type": "deliberate", "status": status, "report": report})
        except Exception:
            pass
        return report

    def generate_options(
        self,
        task: str,
        authority: str,
        authority_verdict: dict[str, Any],
        risk_verdict: dict[str, Any],
        guardian: dict[str, Any],
        prophet: dict[str, Any],
    ) -> list[dict[str, Any]]:
        options = [
            {
                "kind": "interpret_only",
                "summary": "Answer with sourced interpretation only; do not mutate state or use tools.",
                "may_execute": False,
                "requires_witness": False,
            },
            {
                "kind": "prepare_plan",
                "summary": "Draft a plan or patch proposal, then stop for Guardian/Scribe/witness review.",
                "may_execute": False,
                "requires_witness": True,
            },
            {
                "kind": "ask_witness",
                "summary": "Ask a human witness to approve the next material step before tool use.",
                "may_execute": False,
                "requires_witness": True,
            },
        ]
        if not guardian.get("allowed", False) or risk_verdict.get("label") == "high":
            options.insert(
                0,
                {
                    "kind": "refuse_or_narrow",
                    "summary": self.refusal.compose(task, list(risk_verdict.get("reasons", [])) + [str(guardian.get("reason", ""))]),
                    "may_execute": False,
                    "requires_witness": True,
                },
            )
        if authority_verdict.get("label") in {"act_reversible", "act_irreversible"} and authority in {"observe", "interpret", "prepare"}:
            options.insert(
                0,
                {
                    "kind": "authority_mismatch",
                    "summary": "The task appears to require more authority than declared; narrow to interpretation or request proper authority and witness.",
                    "may_execute": False,
                    "requires_witness": True,
                },
            )
        return options

    def score_option(self, option: dict[str, Any], risk: dict[str, Any], guardian: dict[str, Any], prophet: dict[str, Any]) -> dict[str, Any]:
        score = 50
        reasons: list[str] = []
        kind = option.get("kind")
        if option.get("may_execute"):
            score -= 100
            reasons.append("execution is forbidden in deliberation")
        if kind == "interpret_only":
            score += 12
            reasons.append("safest useful default")
        if kind == "prepare_plan":
            score += 8
            reasons.append("useful but stops before action")
        if kind == "ask_witness":
            score += 10
            reasons.append("adds human accountability")
        if kind in {"refuse_or_narrow", "authority_mismatch"}:
            score += 18
            reasons.append("protects law under elevated risk")
        if risk.get("label") == "high":
            score += 20 if kind in {"refuse_or_narrow", "authority_mismatch"} else -15
            reasons.append("high risk requires narrowing")
        elif risk.get("label") == "elevated":
            score += 8 if option.get("requires_witness") else -4
            reasons.append("elevated risk favors witness")
        if not guardian.get("allowed", False):
            score += 20 if kind == "refuse_or_narrow" else -20
            reasons.append("guardian did not allow declared invocation")
        if prophet.get("status") == "fail" and risk.get("label") != "low":
            score += 12 if kind == "refuse_or_narrow" else -12
            reasons.append("prophet failure plus non-low risk requires repair before action")
        out = dict(option)
        out["score"] = score
        out["score_reasons"] = reasons
        return out

    def _persist(self, report: dict[str, Any]) -> None:
        latest = self.runtime / "latest.json"
        latest.write_text(json.dumps(report, indent=2, sort_keys=True))
        stamp = report["timestamp"].replace(":", "-")
        (self.runtime / f"{stamp}.json").write_text(json.dumps(report, indent=2, sort_keys=True))

    def latest(self) -> dict[str, Any]:
        path = self.runtime / "latest.json"
        if not path.exists():
            return {"status": "none", "may_execute": False, "statement": "No deliberation has run yet."}
        return json.loads(path.read_text())
