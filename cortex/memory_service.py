"""Governed typed memory service for Cortex."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TYPES = {"factual", "inferred", "symbolic", "project", "rejected", "personal"}


class MemoryService:
    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root).resolve()
        self.dir = self.root / "memory"
        self.dir.mkdir(parents=True, exist_ok=True)

    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _path(self, typ: str) -> Path:
        if typ not in TYPES:
            raise ValueError(f"unknown memory type: {typ}")
        return self.dir / f"{typ}.jsonl"

    def write(self, typ: str, content: str, source: str, confidence: float = 0.8, witness: str | None = None) -> dict[str, Any]:
        if not source:
            raise ValueError("memory source is required")
        if typ == "personal" and not witness:
            raise ValueError("personal memory requires witness")
        rec = {
            "id": "mem_" + uuid.uuid4().hex[:12],
            "type": typ,
            "content": content,
            "source": source,
            "confidence": confidence,
            "created_at": self.now(),
            "mutable": True,
            "witness": witness,
            "sha256": hashlib.sha256(f"{typ}:{content}:{source}".encode()).hexdigest(),
            "law": ["LAW 6", "LAW 7"],
        }
        with self._path(typ).open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")
        return rec

    def retrieve(self, query: str = "", typ: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        paths = [self._path(typ)] if typ else [self.dir / f"{t}.jsonl" for t in sorted(TYPES)]
        rows: list[dict[str, Any]] = []
        q = query.lower()
        for path in paths:
            if not path.exists():
                continue
            for line in path.read_text().splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                if not q or q in rec.get("content", "").lower() or q in rec.get("source", "").lower():
                    rows.append(rec)
        return rows[-limit:]
