"""Relationship/profile layer for Cortex mobile.

Cortex learns the human only through explicit, witnessed personal memories.
This is not covert profiling: memories are readable, auditable, and sourced.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cortex.memory_service import MemoryService


class RelationshipService:
    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root).resolve()
        self.memory = MemoryService(self.root)

    def profile(self, limit: int = 12) -> dict[str, Any]:
        records = self.memory.retrieve(typ="personal", limit=limit)
        facts = [r.get("content", "") for r in records if r.get("content")]
        return {
            "status": "ok",
            "relationship": "human_known_through_witnessed_memory",
            "summary": self._summary(facts),
            "facts": facts[-limit:],
            "records": records,
            "may_execute": False,
        }

    def remember(self, content: str, witness: str | None, source: str = "mobile_chat") -> dict[str, Any]:
        if not content.strip():
            return {"status": "refused", "reason": "content is required", "may_execute": False}
        rec = self.memory.write("personal", content.strip(), source, 0.85, witness)
        return {"status": "remembered", "record": rec, "may_execute": False}

    def _summary(self, facts: list[str]) -> str:
        if not facts:
            return "I do not know the human yet. Invite them to share preferences, goals, constraints, and context."
        latest = facts[-5:]
        return "What I currently know: " + " ".join(f"{i+1}. {fact}" for i, fact in enumerate(latest))
