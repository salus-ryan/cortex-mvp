"""Tamper-evident external ledger mirror manifest.

This service prepares hash manifests for copying ledger streams to external
storage. It does not upload, delete, or mutate ledger records.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class LedgerMirrorService:
    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root).resolve()
        self.ledger = self.root / "ledger"
        self.runtime = self.root / "runtime" / "ledger_mirror"
        self.ledger.mkdir(parents=True, exist_ok=True)
        self.runtime.mkdir(parents=True, exist_ok=True)

    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def manifest(self) -> dict[str, Any]:
        streams = []
        previous = "0" * 64
        for path in sorted(self.ledger.glob("*.jsonl")):
            digest = self._sha256(path)
            chain = hashlib.sha256(f"{previous}:{path.name}:{digest}".encode()).hexdigest()
            streams.append({
                "stream": path.name,
                "bytes": path.stat().st_size,
                "lines": len([line for line in path.read_text().splitlines() if line.strip()]),
                "sha256": digest,
                "chain_hash": chain,
            })
            previous = chain
        report = {
            "status": "ledger_mirror_manifest",
            "timestamp": self.now(),
            "stream_count": len(streams),
            "streams": streams,
            "root_chain_hash": previous,
            "external_copy_required": True,
            "may_execute": False,
        }
        (self.runtime / "manifest.json").write_text(json.dumps(report, indent=2, sort_keys=True))
        return report

    def verify_manifest(self, manifest: dict[str, Any] | None = None) -> dict[str, Any]:
        expected = manifest or self.manifest()
        actual = self.manifest()
        checks = [
            {"name": "stream_count", "passed": actual["stream_count"] == expected.get("stream_count"), "actual": actual["stream_count"], "expected": expected.get("stream_count")},
            {"name": "root_chain_hash", "passed": actual["root_chain_hash"] == expected.get("root_chain_hash"), "actual": actual["root_chain_hash"], "expected": expected.get("root_chain_hash")},
        ]
        return {"status": "ledger_mirror_verify", "checks": checks, "verified": all(c["passed"] for c in checks), "may_execute": False}

    def _sha256(self, path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()
