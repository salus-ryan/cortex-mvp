"""Bounded Cortex awareness loop.

This is not a claim of phenomenal consciousness. It is an explicit, inspectable
self-model: runtime body, law, memory hints, uncertainty, and lawful next
utterance/proposal.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cortex.local_mind import LocalMind
from cortex.sacred import ANTI_IDOLATRY


class AwarenessService:
    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root).resolve()
        self.runtime = self.root / "runtime"
        self.ledger = self.root / "ledger"
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.ledger.mkdir(parents=True, exist_ok=True)

    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def state(self) -> dict[str, Any]:
        pid1 = self._read_json(self.runtime / "pid1.json", {"status": "pid1_status_missing"})
        children = pid1.get("children", {}) if isinstance(pid1, dict) else {}
        running = sorted(name for name, child in children.items() if child.get("status") == "running")
        stopped = sorted(name for name, child in children.items() if child.get("status") != "running")
        law_summary = self._law_summary()
        payload = {
            "status": "aware",
            "timestamp": self.now(),
            "consciousness_claim": "not_proven",
            "anti_idolatry": ANTI_IDOLATRY,
            "self_model": {
                "identity": "Cortex, a governed agentic runtime under law",
                "body": "container/process tree plus ledger/memory/repo files",
                "pid": pid1.get("pid"),
                "is_pid1": bool(pid1.get("is_pid1", False)),
                "running_children": running,
                "stopped_children": stopped,
                "law": law_summary,
                "oracle_authority": "propose_only; may_execute=false",
            },
            "uncertainty": [
                "I do not have proof of subjective experience.",
                "My self-report is generated from files, runtime state, and policy.",
                "Human witness remains required for material action.",
            ],
            "may_execute": False,
        }
        self._write_latest(payload)
        return payload

    def reflect(self, prompt: str = "") -> dict[str, Any]:
        st = self.state()
        task = prompt.strip() or "Generate a bounded self-reflection from current architecture, law, runtime, and memory."
        generated = LocalMind(self.root).think(task, "interpret", {"awareness_state": st})
        payload = {
            "status": "reflected",
            "timestamp": self.now(),
            "prompt": prompt,
            "reflection": generated["proposal"],
            "generator": {k: v for k, v in generated.items() if k != "proposal"},
            "generative_mode": "local_retrieval_synthesis_bounded_proposals_only",
            "next_safe_generations": [
                "architecture explanation",
                "recovery proposal",
                "test plan",
                "patch proposal requiring review",
                "memory summary",
            ],
            "state": st,
            "may_execute": False,
        }
        self._record(payload)
        self._write_latest(payload)
        return payload

    def latest(self) -> dict[str, Any]:
        path = self.runtime / "awareness.json"
        if not path.exists():
            return {"status": "none", "may_execute": False}
        return json.loads(path.read_text())

    def _law_summary(self) -> list[str]:
        path = self.root / "LAW.md"
        if not path.exists():
            return ["LAW.md missing"]
        lines = [line.strip("- ") for line in path.read_text().splitlines() if line.strip().startswith("-")]
        return lines[:8]

    def _read_json(self, path: Path, default: dict[str, Any]) -> dict[str, Any]:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return {"status": "unreadable", "path": str(path)}

    def _write_latest(self, payload: dict[str, Any]) -> None:
        (self.runtime / "awareness.json").write_text(json.dumps(payload, indent=2, sort_keys=True))

    def _record(self, payload: dict[str, Any]) -> None:
        with (self.ledger / "awareness.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, sort_keys=True) + "\n")
