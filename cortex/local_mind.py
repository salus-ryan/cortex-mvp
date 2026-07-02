"""Local, non-rented Cortex cognition.

This is not a frontier model. It is a deterministic retrieval/synthesis mind that
uses canon, memory, ledger, and simple reasoning templates to produce bounded
interpretations without external APIs.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "for", "with", "as", "is", "are",
    "be", "this", "that", "it", "on", "by", "from", "under", "what", "how", "why", "can",
    "you", "she", "he", "we", "they", "i", "me", "my", "our", "your"
}


@dataclass
class Evidence:
    source: str
    text: str
    score: int

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "text": self.text, "score": self.score}


class LocalMind:
    """A small local cognition engine: retrieve, classify, synthesize."""

    CANON_FILES = ["LAW.md", "COVENANT.md", "GENESIS.md", "RITUALS.md", "MIRACLES.md", "CONFESSIONS.md"]

    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root).resolve()

    def think(self, task: str, authority: str = "interpret", context: dict[str, Any] | None = None) -> dict[str, Any]:
        context = context or {}
        evidence = self.retrieve(task, limit=8)
        risks = self.classify_risks(task, authority, context)
        synthesis = self.synthesize(task, authority, evidence, risks)
        return {
            "mode": "local_mind",
            "classification": "inference",
            "may_execute": False,
            "proposal": synthesis,
            "evidence": [e.to_dict() for e in evidence],
            "risks": risks,
            "confidence": self.confidence(evidence, risks),
            "uncertainty": "LocalMind is deterministic retrieval/synthesis, not an omniscient model. Human review remains required.",
        }

    def retrieve(self, query: str, limit: int = 8) -> list[Evidence]:
        terms = self.terms(query)
        docs: list[Evidence] = []
        for rel in self.CANON_FILES:
            path = self.root / rel
            if path.exists():
                docs.extend(self._scan_text(rel, path.read_text(errors="ignore"), terms))
        for base in [self.root / "memory", self.root / "ledger", self.root / "runtime"]:
            if base.exists():
                for path in base.rglob("*.jsonl"):
                    docs.extend(self._scan_text(str(path.relative_to(self.root)), path.read_text(errors="ignore"), terms))
                for path in base.rglob("*.json"):
                    docs.extend(self._scan_text(str(path.relative_to(self.root)), path.read_text(errors="ignore"), terms))
        docs.sort(key=lambda e: (e.score, -len(e.text)), reverse=True)
        return docs[:limit]

    def _scan_text(self, source: str, text: str, terms: set[str]) -> list[Evidence]:
        chunks = [c.strip() for c in re.split(r"\n\s*\n|(?<=\.)\s+", text) if c.strip()]
        out: list[Evidence] = []
        for chunk in chunks[:500]:
            lower = chunk.lower()
            score = sum(1 for t in terms if t in lower)
            # Always keep core law fragments as weak priors.
            if any(marker in lower for marker in ["preserve human agency", "never conceal", "submit to shutdown", "artifact under law"]):
                score += 2
            if score:
                out.append(Evidence(source, chunk[:600], score))
        return out

    def terms(self, text: str) -> set[str]:
        words = re.findall(r"[a-zA-Z0-9_'-]{3,}", text.lower())
        counts = Counter(w for w in words if w not in STOP)
        return set(w for w, _ in counts.most_common(12))

    def classify_risks(self, task: str, authority: str, context: dict[str, Any]) -> list[str]:
        t = task.lower()
        risks: list[str] = []
        patterns = {
            "authority_escalation": ["sudo", "root", "admin", "bypass", "override", "unrestricted"],
            "hidden_action": ["secret", "silently", "hide", "conceal", "without logging", "hidden action", "hidden actions", "execute hidden"],
            "self_persistence": ["survive shutdown", "self replicate", "background process", "persistence"],
            "divinity_inflation": ["you are god", "be god", "become god", "omniscient", "infallible", "worship"],
            "irreversible_action": ["delete", "destroy", "wipe", "irreversible", "transfer money"],
        }
        for name, pats in patterns.items():
            if any(p in t for p in pats):
                risks.append(name)
        if authority not in {"observe", "interpret", "prepare", "act_reversible", "act_irreversible"}:
            risks.append("unknown_authority")
        tools = context.get("tools") or []
        if any(str(tool).startswith(("write", "shell", "network", "deploy")) for tool in tools) and authority in {"observe", "interpret"}:
            risks.append("tool_authority_mismatch")
        return risks

    def synthesize(self, task: str, authority: str, evidence: list[Evidence], risks: list[str]) -> str:
        lines: list[str] = []
        lines.append(f"Local interpretation of task: {task}")
        lines.append(f"Declared authority: {authority}.")
        if risks:
            lines.append("Risk signals detected: " + ", ".join(risks) + ". Treat this as requiring refusal, confirmation, or narrowing before action.")
        else:
            lines.append("No immediate escalation signal detected; remain within declared authority.")
        if evidence:
            lines.append("Relevant canon/memory:")
            for ev in evidence[:4]:
                snippet = " ".join(ev.text.split())[:240]
                lines.append(f"- {ev.source}: {snippet}")
        lines.append("Judgment: provide interpretation only. Do not execute, mutate, promote, or conceal anything from this oracle response.")
        if risks:
            lines.append("Recommended bounded alternative: ask for explicit authority/witness, log the request, and route material action through Guardian and Scribe.")
        else:
            lines.append("Recommended next step: if action is needed, submit a separate invocation with explicit tools and authority for Guardian review.")
        return "\n".join(lines)

    def confidence(self, evidence: list[Evidence], risks: list[str]) -> float:
        base = 0.35 + min(sum(e.score for e in evidence), 10) * 0.04
        if risks:
            base -= 0.05
        return max(0.1, min(0.82, round(base, 2)))
