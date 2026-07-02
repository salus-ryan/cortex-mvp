"""Witness and governance primitives."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class WitnessService:
    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root).resolve()
        self.ledger = self.root / "ledger"
        self.ledger.mkdir(parents=True, exist_ok=True)

    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def witness(self, name: str, statement: str, scope: str = "general", signature: str | None = None) -> dict[str, Any]:
        text = f"{name}:{scope}:{statement}:{signature or ''}"
        rec = {
            "id": "wit_" + uuid.uuid4().hex[:12],
            "timestamp": self.now(),
            "witness": name,
            "scope": scope,
            "statement": statement,
            "signature": signature,
            "sha256": hashlib.sha256(text.encode()).hexdigest(),
        }
        with (self.ledger / "witnesses.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")
        return rec

    def list(self, scope: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        path = self.ledger / "witnesses.jsonl"
        if not path.exists():
            return []
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        if scope:
            rows = [r for r in rows if r.get("scope") == scope]
        return rows[-limit:]

    def has_scope(self, scope: str, count: int = 1) -> bool:
        return len(self.list(scope, limit=1000)) >= count
