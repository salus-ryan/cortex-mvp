"""Private state vault export/import for Cortex.

Inspired by black-box: local-first, explicit, inspectable state handling. Export is
read-only. Import is narrow, witnessed, and only restores memory JSONL files.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EXPORT_DIRS = {"memory", "ledger"}
IMPORT_PREFIXES = {"memory/"}
DENY_NAMES = {"auth.jsonl", "auth_failures.json", "payments.jsonl"}


class StateService:
    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root).resolve()
        self.ledger = self.root / "ledger"
        self.ledger.mkdir(parents=True, exist_ok=True)

    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def manifest(self) -> dict[str, Any]:
        files = self._files()
        return {
            "status": "ok",
            "offline_trust_posture": self.offline_trust_posture(),
            "exportable_files": len(files),
            "bytes": sum(f["bytes"] for f in files),
            "files": [{k: v for k, v in f.items() if k != "content"} for f in files],
            "may_execute": False,
        }

    def export(self) -> dict[str, Any]:
        files = self._files(include_content=True)
        body = {
            "format": "cortex-state-v1",
            "created_at": self.now(),
            "offline_trust_posture": self.offline_trust_posture(),
            "files": files,
        }
        encoded = json.dumps(body, sort_keys=True)
        return {
            "status": "ok",
            "bundle": body,
            "sha256": hashlib.sha256(encoded.encode()).hexdigest(),
            "may_execute": False,
        }

    def import_bundle(self, bundle: dict[str, Any], witness: str | None, confirmed: bool = False) -> dict[str, Any]:
        if not witness:
            return self._refuse("state import requires witness")
        if not confirmed:
            return self._refuse("state import requires confirmed=true")
        if bundle.get("format") != "cortex-state-v1":
            return self._refuse("unsupported state bundle format")
        restored: list[str] = []
        for file in bundle.get("files", []):
            rel = str(file.get("path", ""))
            if not any(rel.startswith(prefix) for prefix in IMPORT_PREFIXES):
                continue
            if Path(rel).name in DENY_NAMES or ".." in Path(rel).parts:
                continue
            content = str(file.get("content", ""))
            expected = str(file.get("sha256", ""))
            if expected and hashlib.sha256(content.encode()).hexdigest() != expected:
                return self._refuse(f"sha256 mismatch for {rel}")
            dest = (self.root / rel).resolve()
            if not str(dest).startswith(str(self.root)):
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open("a", encoding="utf-8") as f:
                if content and not content.endswith("\n"):
                    content += "\n"
                f.write(content)
            restored.append(rel)
        rec = {"timestamp": self.now(), "actor": "state", "action_type": "state_import", "status": "imported", "witness": witness, "restored": restored}
        self._append_ledger(rec)
        return {"status": "imported", "restored": restored, "record": rec, "may_execute": False}

    def offline_trust_posture(self) -> dict[str, Any]:
        return {
            "local_first": True,
            "exports_are_explicit": True,
            "imports_require_witness_and_confirmation": True,
            "secrets_excluded": True,
            "railway_persistence_warning": "Railway filesystem may be ephemeral unless a volume is configured.",
            "offline_mode_requested": os.environ.get("CORTEX_OFFLINE_MODE", "0").lower() in {"1", "true", "yes"},
        }

    def _files(self, include_content: bool = False) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for dirname in sorted(EXPORT_DIRS):
            base = self.root / dirname
            if not base.exists():
                continue
            for path in sorted(base.rglob("*.jsonl")):
                if path.name in DENY_NAMES:
                    continue
                rel = str(path.relative_to(self.root))
                content = path.read_text(errors="ignore")
                item = {"path": rel, "bytes": len(content.encode()), "sha256": hashlib.sha256(content.encode()).hexdigest()}
                if include_content:
                    item["content"] = content
                out.append(item)
        return out

    def _append_ledger(self, rec: dict[str, Any]) -> None:
        with (self.ledger / "state.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")

    def _refuse(self, reason: str) -> dict[str, Any]:
        rec = {"timestamp": self.now(), "actor": "state", "action_type": "state_import", "status": "refused", "reason": reason}
        self._append_ledger(rec)
        return {"status": "refused", "reason": reason, "record": rec, "may_execute": False}
