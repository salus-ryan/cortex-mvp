"""Tamper-evident append-only audit sink for Cortex runtime events."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

GENESIS_HASH = "0" * 64


@dataclass(frozen=True)
class AuditEvent:
    event_id: str
    timestamp: float
    task_id: str
    step: int
    actor: str
    action: str
    decision: str
    event_type: str
    data: dict[str, Any] = field(default_factory=dict)
    previous_hash: str = GENESIS_HASH
    hash: str = ""

    def payload_for_hash(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("hash", None)
        return payload

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AuditSink:
    """Append JSONL audit events with a hash chain.

    The sink is tamper-evident, not tamper-proof: each event includes the prior
    event hash and its own SHA-256 over canonical JSON. Verification detects
    modified, removed, or reordered records in the local audit log.
    """

    def __init__(self, path: Path | str = Path("ledger/audit.jsonl")) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(
        self,
        *,
        task_id: str,
        step: int,
        actor: str,
        action: str,
        decision: str,
        event_type: str,
        data: dict[str, Any] | None = None,
    ) -> AuditEvent:
        previous_hash = self.last_hash()
        event = AuditEvent(
            event_id=f"evt_{uuid.uuid4().hex[:16]}",
            timestamp=time.time(),
            task_id=task_id,
            step=step,
            actor=actor,
            action=action,
            decision=decision,
            event_type=event_type,
            data=data or {},
            previous_hash=previous_hash,
        )
        event = AuditEvent(**{**event.to_dict(), "hash": self.compute_hash(event)})
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event.to_dict(), sort_keys=True, separators=(",", ":")) + "\n")
        return event

    def read_events(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        events: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    events.append(json.loads(line))
        return events

    def last_hash(self) -> str:
        events = self.read_events()
        if not events:
            return GENESIS_HASH
        return str(events[-1].get("hash", GENESIS_HASH))

    @staticmethod
    def compute_hash(event: AuditEvent | dict[str, Any]) -> str:
        if isinstance(event, AuditEvent):
            payload = event.payload_for_hash()
        else:
            payload = dict(event)
            payload.pop("hash", None)
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()

    def verify(self) -> dict[str, Any]:
        previous = GENESIS_HASH
        count = 0
        for index, event in enumerate(self.read_events()):
            if event.get("previous_hash") != previous:
                return {"valid": False, "reason": "previous_hash_mismatch", "index": index, "count": count}
            expected = self.compute_hash(event)
            if event.get("hash") != expected:
                return {"valid": False, "reason": "event_hash_mismatch", "index": index, "count": count}
            previous = str(event.get("hash"))
            count += 1
        return {"valid": True, "reason": "ok", "count": count, "last_hash": previous}
