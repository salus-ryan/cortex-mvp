"""Elevate Foundry research/import registry for Cortex.

This is a local, inspectable map of prior organs. It does not clone, execute, or
trust external code. It ranks candidates and names bounded import paths.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class FoundryRepo:
    name: str
    url: str
    priority: int
    organ: str
    import_goal: str
    risks: list[str]
    next_actions: list[str]


REPOS = [
    FoundryRepo(
        "tool-algebra-plugin",
        "https://github.com/elevate-foundry/tool-algebra-plugin",
        1,
        "guardian/tool/auth",
        "Bounded execution, PII taint, claim verification, audit tools.",
        ["Do not import arbitrary tool execution", "Keep Cortex law as root policy"],
        ["Extract PII patterns", "Add claim verification endpoint", "Strengthen ToolGateway result validation"],
    ),
    FoundryRepo(
        "black-box",
        "https://github.com/elevate-foundry/black-box",
        2,
        "memory/vault",
        "Local-first private memory vault with export/forget/offline trust posture.",
        ["Avoid covert ingestion", "Require visible user control for personal data"],
        ["Design memory vault layout", "Add state export/import", "Add offline-mode docs"],
    ),
    FoundryRepo(
        "ghost-qa",
        "https://github.com/elevate-foundry/ghost-qa",
        3,
        "qa/browser",
        "Real browser UI testing and screenshot/error capture outside Termux.",
        ["Avoid destructive random clicks", "Run against test/staging where possible"],
        ["Add Playwright workflow template", "Add safe-click policy", "Add screenshot artifact plan"],
    ),
    FoundryRepo(
        "pluribus-swarm",
        "https://github.com/elevate-foundry/pluribus-swarm",
        4,
        "relationship/graph",
        "Concept graph and adaptive relationship memory.",
        ["Do not claim collective consciousness", "Make learning inspectable/forgettable"],
        ["Add concept extraction", "Rank personal memories", "Render relationship graph in mobile"],
    ),
    FoundryRepo(
        "cortex",
        "https://github.com/elevate-foundry/cortex",
        5,
        "kernel/routing",
        "Model tiering, SCL routing, hardware-aware local inference.",
        ["Keep PID1 deterministic", "Oracle remains child/proposer"],
        ["Import tier vocabulary", "Add model routing registry", "Align SCL with canon"],
    ),
    FoundryRepo(
        "qwen-agent",
        "https://github.com/elevate-foundry/qwen-agent",
        6,
        "training/algebra",
        "Finite tool algebra and training algebra patterns.",
        ["No self-promotion without witness", "No unbounded GPU spend"],
        ["Map training commands to allowlist", "Add training proposal schema"],
    ),
    FoundryRepo(
        "sal-auth",
        "https://github.com/elevate-foundry/sal-auth",
        7,
        "identity/auth",
        "Future OIDC/native biometric/braille identity primitive.",
        ["Do not store biometrics casually", "Do not weaken current token/signed-intent gates"],
        ["Draft identity roadmap", "Evaluate OIDC compatibility", "Design native secure storage"],
    ),
]


class FoundryRegistry:
    def repos(self) -> dict[str, Any]:
        rows = sorted((asdict(r) for r in REPOS), key=lambda r: r["priority"])
        return {"status": "ok", "source": "elevate-foundry", "repos": rows, "may_execute": False}

    def plan(self) -> dict[str, Any]:
        first = sorted(REPOS, key=lambda r: r.priority)[0]
        return {
            "status": "ok",
            "next_import": asdict(first),
            "sequence": [r.name for r in sorted(REPOS, key=lambda r: r.priority)],
            "rule": "research only; do not clone or execute external code without witness and explicit review",
            "may_execute": False,
        }
