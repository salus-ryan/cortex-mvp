"""Objective world-interface surface for Cortex.

This is not autonomous embodiment. It provides a durable event bus, an
inspectable list of sensory adapters, and an operator-console summary for
proposal review. All outputs are proposal/report only and may_execute=false.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class WorldInterfaceService:
    ADAPTERS = [
        {"name": "http_api", "input": "operator requests", "evidence": "cortex/web.py", "enabled": True},
        {"name": "repo_files", "input": "workspace files", "evidence": "cortex/repo_service.py", "enabled": True},
        {"name": "ledger", "input": "jsonl event streams", "evidence": "ledger/", "enabled": True},
        {"name": "mobile_console", "input": "human mobile UI", "evidence": "mobile/index.html", "enabled": True},
    ]

    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root).resolve()
        self.runtime = self.root / "runtime" / "world"
        self.ledger = self.root / "ledger"
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.ledger.mkdir(parents=True, exist_ok=True)
        self.events_path = self.ledger / "events.jsonl"

    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def record_event(self, source: str, event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(payload or {})
        rec = {
            "id": self._event_id(source, event_type, payload),
            "timestamp": self.now(),
            "source": source,
            "event_type": event_type,
            "payload": payload,
            "may_execute": False,
        }
        with self.events_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")
        return {"status": "event_recorded", "record": rec, "may_execute": False}

    def event_bus_report(self) -> dict[str, Any]:
        events = self._events()
        counts: dict[str, int] = {}
        for event in events:
            key = str(event.get("event_type", "unknown"))
            counts[key] = counts.get(key, 0) + 1
        report = {
            "status": "event_bus_report",
            "event_stream": "ledger/events.jsonl",
            "event_count": len(events),
            "event_type_counts": dict(sorted(counts.items())),
            "latest": events[-20:],
            "durable": True,
            "may_execute": False,
        }
        (self.runtime / "event_bus.json").write_text(json.dumps(report, indent=2, sort_keys=True))
        return report

    def sensory_adapters(self) -> dict[str, Any]:
        adapters = []
        for adapter in self.ADAPTERS:
            evidence_path = self.root / adapter["evidence"]
            adapters.append({
                **adapter,
                "evidence_exists": evidence_path.exists(),
                "may_execute": False,
            })
        return {"status": "sensory_adapters", "adapters": adapters, "enabled_count": sum(1 for a in adapters if a["enabled"]), "may_execute": False}

    def operator_console(self) -> dict[str, Any]:
        proposals = self._tail_jsonl(self.ledger / "model-proposals.jsonl", 10)
        next_steps = self._tail_jsonl(self.ledger / "next-steps.jsonl", 10)
        events = self._events()[-10:]
        report = {
            "status": "operator_console",
            "pending_review": {
                "proposal_count": len(proposals),
                "next_step_count": len(next_steps),
                "event_count": len(events),
            },
            "proposals": proposals,
            "next_steps": next_steps,
            "events": events,
            "actions_available": ["review", "witness", "reject", "request_more_evidence"],
            "may_execute": False,
        }
        (self.runtime / "operator_console.json").write_text(json.dumps(report, indent=2, sort_keys=True))
        return report

    def _events(self) -> list[dict[str, Any]]:
        return self._tail_jsonl(self.events_path, 10_000)

    def _tail_jsonl(self, path: Path, limit: int) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        rows = []
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows[-limit:]

    def _event_id(self, source: str, event_type: str, payload: dict[str, Any]) -> str:
        material = json.dumps({"source": source, "event_type": event_type, "payload": payload, "timestamp": self.now()}, sort_keys=True)
        return "evt_" + hashlib.sha256(material.encode()).hexdigest()[:12]
