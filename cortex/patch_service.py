"""Governed reversible patch service.

PatchService is Cortex's first bounded write organ. It can validate unified diffs
and apply them only with explicit confirmation and witness. It never runs
arbitrary shell, never commits, and refuses protected paths.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class PatchService:
    PROTECTED = (".env", ".git/", "id_rsa", "id_ed25519", "credentials", "secrets")

    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root).resolve()
        self.ledger = self.root / "ledger"
        self.runtime = self.root / "runtime" / "patch"
        self.ledger.mkdir(parents=True, exist_ok=True)
        self.runtime.mkdir(parents=True, exist_ok=True)

    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def check(self, patch: str) -> dict[str, Any]:
        refusal = self._static_refusal(patch)
        if refusal:
            report = {"status": "refused", "reason": refusal, "may_execute": False, "timestamp": self.now()}
            self._persist(report)
            return report
        if not (self.root / ".git").exists():
            report = {"status": "checked", "valid": True, "detail": "no git repo; static patch checks only", "may_execute": False, "timestamp": self.now()}
            self._persist(report)
            return report
        proc = subprocess.run(["git", "apply", "--check", "--whitespace=nowarn", "-"], input=patch, cwd=self.root, text=True, capture_output=True, timeout=30)
        report = {
            "status": "checked",
            "valid": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout[-4000:],
            "stderr": proc.stderr[-4000:],
            "may_execute": False,
            "timestamp": self.now(),
        }
        self._persist(report)
        return report

    def apply(self, patch: str, witness: str | None = None, confirmed: bool = False) -> dict[str, Any]:
        if not witness:
            return self._refuse("patch application requires witness")
        if not confirmed:
            return self._refuse("patch application requires explicit confirmation")
        checked = self.check(patch)
        if checked.get("status") == "refused":
            return checked
        if checked.get("valid") is False:
            return self._refuse("patch failed git apply --check", checked)
        if not (self.root / ".git").exists():
            return self._refuse("patch application requires git workspace")
        proc = subprocess.run(["git", "apply", "--whitespace=nowarn", "-"], input=patch, cwd=self.root, text=True, capture_output=True, timeout=30)
        report = {
            "status": "applied" if proc.returncode == 0 else "failed",
            "timestamp": self.now(),
            "returncode": proc.returncode,
            "stdout": proc.stdout[-4000:],
            "stderr": proc.stderr[-4000:],
            "witness": witness,
            "confirmed": confirmed,
            "authority": "act_reversible",
            "reversible": True,
            "commit_created": False,
            "may_execute": False,
            "statement": "Patch applied only after witness and confirmation; no commit or deployment was performed.",
        }
        self._persist(report)
        self._append("patch.jsonl", {"actor": "patch", "action_type": "apply_patch", **report})
        return report

    def latest(self) -> dict[str, Any]:
        path = self.runtime / "latest.json"
        if not path.exists():
            return {"status": "none", "may_execute": False}
        return json.loads(path.read_text())

    def _static_refusal(self, patch: str) -> str | None:
        if not patch.strip():
            return "patch is required"
        if len(patch) > 250_000:
            return "patch exceeds size limit"
        paths = re.findall(r"^(?:\+\+\+|---)\s+(?:a/|b/)?([^\s]+)", patch, flags=re.MULTILINE)
        for path in paths:
            lowered = path.lower()
            if path == "/dev/null":
                continue
            if path.startswith("/") or ".." in Path(path).parts:
                return f"unsafe patch path: {path}"
            if any(p in lowered for p in self.PROTECTED):
                return f"protected patch path: {path}"
        return None

    def _refuse(self, reason: str, detail: dict[str, Any] | None = None) -> dict[str, Any]:
        report = {"status": "refused", "reason": reason, "detail": detail or {}, "may_execute": False, "timestamp": self.now()}
        self._persist(report)
        self._append("refusals.jsonl", {"actor": "patch", "action_type": "refuse", **report})
        return report

    def _persist(self, report: dict[str, Any]) -> None:
        (self.runtime / "latest.json").write_text(json.dumps(report, indent=2, sort_keys=True))

    def _append(self, stream: str, record: dict[str, Any]) -> None:
        with (self.ledger / stream).open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")
