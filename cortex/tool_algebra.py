"""Small Cortex tool algebra inspired by elevate-foundry/tool-algebra-plugin.

Imported as principles, not code: bounded outputs, PII taint detection,
truncation signals, and claim verification against observable evidence.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any


PII_PATTERNS = {
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "phone": re.compile(r"(?<!\d)(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?){2}\d{4}(?!\d)"),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "secret_key": re.compile(r"\b(?:sk|pk|ghp|github_pat|rk|railway)_[A-Za-z0-9_\-]{12,}\b", re.I),
    "bearer": re.compile(r"Bearer\s+[A-Za-z0-9._\-]{16,}", re.I),
}


@dataclass(frozen=True)
class Taint:
    kind: str
    count: int

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "count": self.count}


class ToolAlgebra:
    MAX_OUTPUT_CHARS = 10_000

    def scan_pii(self, text: str) -> list[Taint]:
        out: list[Taint] = []
        for kind, pattern in PII_PATTERNS.items():
            count = len(pattern.findall(text))
            if count:
                out.append(Taint(kind, count))
        return out

    def redact(self, text: str) -> str:
        redacted = text
        for kind, pattern in PII_PATTERNS.items():
            redacted = pattern.sub(f"[REDACTED:{kind}]", redacted)
        return redacted

    def validate_output(self, tool: str, output: Any) -> dict[str, Any]:
        text = output if isinstance(output, str) else repr(output)
        taints = self.scan_pii(text)
        truncated = len(text) > self.MAX_OUTPUT_CHARS
        safe_text = self.redact(text[: self.MAX_OUTPUT_CHARS])
        return {
            "status": "validated",
            "tool": tool,
            "output_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "pii_taints": [t.to_dict() for t in taints],
            "redacted": bool(taints),
            "truncated": truncated,
            "safe_output": safe_text,
            "law": ["LAW 2", "LAW 6", "LAW 7"],
            "may_execute": False,
        }

    def verify_claim(self, claim: str, evidence: list[str]) -> dict[str, Any]:
        claim_terms = self._terms(claim)
        evidence_text = "\n".join(evidence)
        evidence_terms = self._terms(evidence_text)
        overlap = sorted(claim_terms & evidence_terms)
        unsupported = sorted(claim_terms - evidence_terms)
        score = 0.0 if not claim_terms else round(len(overlap) / len(claim_terms), 2)
        status = "supported" if score >= 0.45 and len(overlap) >= 2 else "unsupported"
        return {
            "status": status,
            "claim": claim,
            "score": score,
            "overlap_terms": overlap[:20],
            "unsupported_terms": unsupported[:20],
            "evidence_count": len(evidence),
            "judgment": "claim has observable support" if status == "supported" else "claim needs stronger observable evidence",
            "may_execute": False,
        }

    def _terms(self, text: str) -> set[str]:
        stop = {"the", "and", "for", "that", "with", "this", "from", "have", "has", "are", "was", "were", "you", "your", "into", "will", "can", "not"}
        return {w for w in re.findall(r"[a-zA-Z][a-zA-Z0-9_'-]{2,}", text.lower()) if w not in stop}
