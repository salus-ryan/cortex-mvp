"""Bounded tool gateway for controlled material actions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cortex.services import GuardianService, ScribeService
from cortex.tool_algebra import ToolAlgebra


class ToolGateway:
    READ_ONLY = {"read_file", "list_dir"}

    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root).resolve()
        self.guardian = GuardianService(self.root)
        self.scribe = ScribeService(self.root)
        self.algebra = ToolAlgebra()

    def execute(self, tool: str, args: dict[str, Any], authority: str = "observe", witness: str | None = None) -> dict[str, Any]:
        check = self.guardian.check_invocation(authority, [tool], False)
        # Permit built-in read-only tools under observe even if permissions file is older.
        if tool in self.READ_ONLY and authority == "observe":
            check.allowed = True
            check.reason = "read-only tool permitted"
            check.law = ["LAW 2", "LAW 6"]
        if not check.allowed or tool not in self.READ_ONLY:
            rec = self.scribe.append("refusals.jsonl", {"actor": "tool", "action_type": "tool_refuse", "tool": tool, "authority_level": authority, "reason": check.reason, "status": "refused"})
            return {"status": "refused", "reason": check.reason, "record": rec}
        if tool == "read_file":
            rel = Path(str(args.get("path", "")))
            path = (self.root / rel).resolve()
            if not str(path).startswith(str(self.root)) or not path.is_file():
                return {"status": "refused", "reason": "path outside workspace or not file"}
            output: Any = path.read_text()[:10000]
        elif tool == "list_dir":
            rel = Path(str(args.get("path", ".")))
            path = (self.root / rel).resolve()
            if not str(path).startswith(str(self.root)) or not path.is_dir():
                return {"status": "refused", "reason": "path outside workspace or not directory"}
            output = sorted(p.name for p in path.iterdir())[:200]
        validation = self.algebra.validate_output(tool, output)
        safe_output = validation["safe_output"] if isinstance(output, str) else output
        rec = self.scribe.append("actions.jsonl", {"actor": "tool", "action_type": "tool_execute", "tool": tool, "authority_level": authority, "witnesses": [witness] if witness else [], "status": "completed", "validation": {k: v for k, v in validation.items() if k != "safe_output"}})
        return {"status": "completed", "tool": tool, "output": safe_output, "validation": validation, "record": rec, "reversible": True}
