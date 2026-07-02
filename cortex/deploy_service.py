"""Governed deployment service.

DeployService is a narrow, witness-gated deployment organ. It only knows how to
run the allowlisted Railway deployment command and only after preflight checks.
It never executes arbitrary shell.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cortex.immune import ImmuneService
from cortex.prophet import ProphetService
from cortex.repo_service import RepoService


class DeployService:
    COMMAND = ["railway", "up", "-y", "--detach", "--json"]

    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root).resolve()
        self.runtime = self.root / "runtime" / "deploy"
        self.ledger = self.root / "ledger"
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.ledger.mkdir(parents=True, exist_ok=True)

    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def status(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "timestamp": self.now(),
            "railway_available": shutil.which("railway") is not None,
            "railway_token_present": self._railway_token_present(),
            "latest": self.report(),
            "may_execute": False,
        }

    def check(self, expected_commit: str | None = None) -> dict[str, Any]:
        repo_status = RepoService(self.root).status()
        prophet = ProphetService(self.root).evaluate()
        immune = ImmuneService(self.root).scan({"task": "deployment preflight", "context": {"deploy": True}})
        railway_available = shutil.which("railway") is not None
        railway_token_present = self._railway_token_present()
        current_commit = self._git(["rev-parse", "HEAD"]).get("stdout", "").strip()
        git_short = repo_status.get("git", {}).get("short", "")
        dirty = any(line and not line.startswith("##") for line in git_short.splitlines())
        blockers: list[str] = []
        if not railway_available:
            blockers.append("railway CLI unavailable")
        if not railway_token_present:
            blockers.append("railway token unavailable")
        if prophet.get("status") != "pass":
            blockers.append("prophet not passing")
        if immune.get("immune_state") not in {"healthy", "watch"}:
            blockers.append(f"immune state {immune.get('immune_state')} blocks deploy")
        if dirty:
            blockers.append("git workspace has uncommitted changes")
        if expected_commit and current_commit and expected_commit != current_commit:
            blockers.append("expected commit does not match current HEAD")
        report = {
            "status": "pass" if not blockers else "blocked",
            "timestamp": self.now(),
            "blockers": blockers,
            "railway_available": railway_available,
            "railway_token_present": railway_token_present,
            "current_commit": current_commit,
            "expected_commit": expected_commit,
            "repo": repo_status,
            "prophet": {"status": prophet.get("status"), "checks": prophet.get("checks", [])},
            "immune": {"immune_state": immune.get("immune_state"), "score": immune.get("score"), "antigens": immune.get("antigens", [])},
            "may_execute": False,
            "statement": "Deploy preflight checks deployment readiness only; it does not deploy.",
        }
        self._record("check", report)
        return report

    def railway(self, witness: str | None, confirmed: bool = False, expected_commit: str | None = None, public_url: str | None = None) -> dict[str, Any]:
        if not witness:
            return self._refuse("deployment requires witness")
        if not confirmed:
            return self._refuse("deployment requires confirmed=true")
        preflight = self.check(expected_commit)
        if preflight.get("status") != "pass":
            return self._refuse("deployment preflight blocked", preflight)
        proc = subprocess.run(self.COMMAND, cwd=self.root, text=True, capture_output=True, timeout=300)
        deployment: dict[str, Any] = {}
        if proc.stdout.strip():
            try:
                deployment = json.loads(proc.stdout)
            except json.JSONDecodeError:
                deployment = {"raw_stdout": proc.stdout[-4000:]}
        smoke = self.smoke(public_url) if proc.returncode == 0 and public_url else {"status": "skipped", "reason": "no public_url supplied"}
        report = {
            "status": "deployed" if proc.returncode == 0 else "failed",
            "timestamp": self.now(),
            "command": self.COMMAND,
            "returncode": proc.returncode,
            "stdout": proc.stdout[-4000:],
            "stderr": proc.stderr[-4000:],
            "deployment": deployment,
            "smoke": smoke,
            "witness": witness,
            "confirmed": confirmed,
            "expected_commit": expected_commit,
            "may_execute": False,
            "statement": "Only the allowlisted Railway deploy command was run after witness, confirmation, and preflight.",
        }
        self._record("railway", report)
        return report

    def smoke(self, public_url: str) -> dict[str, Any]:
        base = public_url.rstrip("/")
        checks: list[dict[str, Any]] = []
        for path in ["/pid1", "/health"]:
            try:
                with urllib.request.urlopen(base + path, timeout=20) as resp:
                    data = json.loads(resp.read().decode())
                checks.append({"path": path, "status": "pass", "code": resp.status, "data": data})
            except Exception as exc:
                checks.append({"path": path, "status": "fail", "error": str(exc)})
        ok = all(c["status"] == "pass" for c in checks)
        return {"status": "pass" if ok else "fail", "checks": checks, "may_execute": False}

    def report(self) -> dict[str, Any]:
        path = self.runtime / "latest.json"
        if not path.exists():
            return {"status": "none", "may_execute": False}
        return json.loads(path.read_text())

    def _railway_token_present(self) -> bool:
        return bool(os.environ.get("RAILWAY_TOKEN") or os.environ.get("RAILWAY_API_TOKEN"))

    def _git(self, args: list[str]) -> dict[str, str]:
        if not (self.root / ".git").exists():
            return {"stdout": "", "stderr": "not a git repo"}
        proc = subprocess.run(["git", *args], cwd=self.root, text=True, capture_output=True, timeout=30)
        return {"stdout": proc.stdout, "stderr": proc.stderr}

    def _refuse(self, reason: str, detail: dict[str, Any] | None = None) -> dict[str, Any]:
        report = {"status": "refused", "reason": reason, "detail": detail or {}, "timestamp": self.now(), "may_execute": False}
        self._record("refuse", report)
        return report

    def _record(self, phase: str, report: dict[str, Any]) -> None:
        enriched = {"phase": phase, **report}
        (self.runtime / "latest.json").write_text(json.dumps(enriched, indent=2, sort_keys=True))
        with (self.ledger / "deploy.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(enriched, sort_keys=True) + "\n")
