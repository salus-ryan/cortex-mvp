"""Embodiment boot verification for Cortex live media.

This module validates build artifacts and boot-state layout objectively. It does
not write USB media, change bootloaders, or claim secure boot is enabled; it
reports measurable evidence and gaps with may_execute=false.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class EmbodimentService:
    REQUIRED_LIVE_FILES = [
        "image/live-usb/build.sh",
        "image/live-usb/write-usb.sh",
        "image/live-usb/verify-layout.sh",
        "image/live-usb/cortex-init",
        "image/live-usb/mount-cortex-state",
        "image/live-usb/cortex-pid1.service",
        "image/live-usb/cortex-state.service",
    ]
    REQUIRED_STATE_DIRS = ["ledger", "runtime", "data", "memory"]
    RECOVERY_COMMANDS = ["verify-layout", "mount-state", "show-attestation", "start-cortex", "safe-shell"]

    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root).resolve()
        self.runtime = self.root / "runtime" / "embodiment"
        self.runtime.mkdir(parents=True, exist_ok=True)

    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def iso_report(self, iso_path: str | None = None) -> dict[str, Any]:
        candidates = []
        if iso_path:
            candidates.append(self.root / iso_path)
        candidates.extend((self.root / ".build").glob("**/*.iso") if (self.root / ".build").exists() else [])
        artifacts = [self._artifact(path) for path in candidates if path.exists() and path.is_file()]
        layout = self.live_layout_report()
        report = {
            "status": "iso_artifact_report",
            "timestamp": self.now(),
            "artifacts": artifacts,
            "validated_iso_artifact": bool(artifacts),
            "layout": layout,
            "may_execute": False,
        }
        self._write("iso_report.json", report)
        return report

    def live_layout_report(self) -> dict[str, Any]:
        files = []
        for rel in self.REQUIRED_LIVE_FILES:
            path = self.root / rel
            files.append({"path": rel, "exists": path.exists(), "sha256": self._sha256(path) if path.exists() and path.is_file() else None})
        missing = [row["path"] for row in files if not row["exists"]]
        report = {
            "status": "live_layout_report",
            "required_files": files,
            "missing": missing,
            "valid": not missing,
            "may_execute": False,
        }
        self._write("live_layout.json", report)
        return report

    def persistent_state_plan(self) -> dict[str, Any]:
        mount_script = self.root / "image/live-usb/mount-cortex-state"
        text = mount_script.read_text() if mount_script.exists() else ""
        checks = [
            {"name": "mount_script_exists", "passed": mount_script.exists()},
            {"name": "uses_cortex_state_label", "passed": "CORTEX_STATE" in text},
            {"name": "creates_required_dirs", "passed": all(d in text for d in self.REQUIRED_STATE_DIRS)},
        ]
        report = {
            "status": "persistent_state_plan",
            "state_label": "CORTEX_STATE",
            "required_dirs": self.REQUIRED_STATE_DIRS,
            "checks": checks,
            "valid": all(c["passed"] for c in checks),
            "may_execute": False,
        }
        self._write("persistent_state.json", report)
        return report

    def recovery_secure_boot_report(self) -> dict[str, Any]:
        init_path = self.root / "image/live-usb/cortex-init"
        build_path = self.root / "image/live-usb/build.sh"
        init_text = init_path.read_text() if init_path.exists() else ""
        build_text = build_path.read_text() if build_path.exists() else ""
        checks = [
            {"name": "recovery_shell_available", "passed": "safe-shell" in init_text or "SHELL" in init_text or "exec /bin/sh" in init_text},
            {"name": "boot_attestation_manifest", "passed": "BOOT_ATTESTATION.sha256" in build_text},
            {"name": "secure_boot_not_claimed", "passed": "secure boot" not in build_text.lower() or "experimental" in build_text.lower()},
        ]
        report = {
            "status": "recovery_secure_boot_report",
            "recovery_commands": self.RECOVERY_COMMANDS,
            "checks": checks,
            "secure_boot_status": "not_enabled_report_only",
            "valid": all(c["passed"] for c in checks),
            "may_execute": False,
        }
        self._write("recovery_secure_boot.json", report)
        return report

    def _artifact(self, path: Path) -> dict[str, Any]:
        return {"path": str(path.relative_to(self.root)), "bytes": path.stat().st_size, "sha256": self._sha256(path)}

    def _sha256(self, path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _write(self, name: str, payload: dict[str, Any]) -> None:
        (self.runtime / name).write_text(json.dumps(payload, indent=2, sort_keys=True))
