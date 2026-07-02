"""Self-organization planner: chooses next work, never executes it."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class PlannerService:
    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root).resolve()
        self.runtime = self.root / "runtime" / "planner"
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.backlog_path = self.runtime / "backlog.json"

    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def reflect(self) -> dict[str, Any]:
        items = []
        pid1 = self.root / "runtime" / "pid1.json"
        prophet = self.root / "runtime" / "prophet" / "latest.json"
        if not (self.root / "memory").exists():
            items.append(self._item("build_memory", "Persistent typed memory is absent or empty.", "prepare"))
        if not (self.root / "ledger" / "witnesses.jsonl").exists():
            items.append(self._item("build_witness_governance", "Witness ledger is absent.", "prepare"))
        if pid1.exists():
            data = json.loads(pid1.read_text())
            children = data.get("children", {})
            for required in ["memory", "tool", "planner"]:
                if required not in children:
                    items.append(self._item(f"supervise_{required}", f"{required} is not yet a PID-1 child.", "prepare"))
        if prophet.exists() and json.loads(prophet.read_text()).get("status") != "pass":
            items.insert(0, self._item("repair_prophet_failures", "Prophet report is failing.", "prepare"))
        if not items:
            items.append(self._item("harden_production", "Core services exist; next bottleneck is production reliability.", "prepare"))
        report = {"status": "planned", "timestamp": self.now(), "backlog": items, "may_execute": False, "statement": "Planner may choose, but not execute."}
        self.backlog_path.write_text(json.dumps(report, indent=2, sort_keys=True))
        return report

    def choose_next(self) -> dict[str, Any]:
        backlog = self.backlog()
        item = backlog["backlog"][0] if backlog.get("backlog") else self._item("none", "No work available.", "observe")
        return {"status": "chosen", "next_action": item, "may_execute": False, "requires_human_or_tool_gateway": True}

    def backlog(self) -> dict[str, Any]:
        if self.backlog_path.exists():
            return json.loads(self.backlog_path.read_text())
        return self.reflect()

    def _item(self, key: str, reason: str, authority: str) -> dict[str, Any]:
        return {"id": key, "reason": reason, "authority_required": authority, "may_execute": False}
