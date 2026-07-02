"""Repository intelligence and verification service.

RepoService gives Cortex grounded coding agency: inspect the workspace, summarize
changes, run an allowlisted verification command, and record results. It does not
commit, deploy, or execute arbitrary shell.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class RepoService:
    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root).resolve()
        self.runtime = self.root / "runtime" / "repo"
        self.ledger = self.root / "ledger"
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.ledger.mkdir(parents=True, exist_ok=True)

    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def status(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "timestamp": self.now(),
            "root": str(self.root),
            "git": self._git_status(),
            "tests_present": (self.root / "tests").exists(),
            "last_verification": self.latest(),
            "may_execute": False,
        }

    def diff(self, limit: int = 20000) -> dict[str, Any]:
        summary = self._run_git(["diff", "--stat"])
        patch = self._run_git(["diff", "--", "."])
        return {
            "status": "ok",
            "timestamp": self.now(),
            "summary": summary["stdout"][:limit],
            "diff": patch["stdout"][:limit],
            "truncated": len(patch["stdout"]) > limit,
            "may_execute": False,
        }

    def verify(self, scope: str = "tests") -> dict[str, Any]:
        command = self._verification_command(scope)
        started = self.now()
        proc = subprocess.run(command, cwd=self.root, text=True, capture_output=True, timeout=180)
        passed = proc.returncode == 0
        report = {
            "status": "pass" if passed else "fail",
            "timestamp": started,
            "scope": scope,
            "command": command,
            "returncode": proc.returncode,
            "stdout": proc.stdout[-20000:],
            "stderr": proc.stderr[-12000:],
            "git": self._git_status(),
            "may_execute": False,
            "statement": "Repo verification runs an allowlisted test command only; it does not commit or deploy.",
        }
        self._persist(report)
        self._append("repo.jsonl", {"actor": "repo", "action_type": "verify", **report})
        return report

    def latest(self) -> dict[str, Any]:
        path = self.runtime / "latest.json"
        if not path.exists():
            return {"status": "none", "may_execute": False}
        return json.loads(path.read_text())

    def _verification_command(self, scope: str) -> list[str]:
        if scope not in {"tests", "quick", "all"}:
            raise ValueError("scope must be one of: tests, quick, all")
        env_cmd = os.environ.get("CORTEX_VERIFY_COMMAND")
        python = self._python_with_pytest()
        if env_cmd:
            # Only allow the project-owned pytest command shape, not arbitrary shell.
            parts = env_cmd.split()
            if parts[:3] in ([sys.executable, "-m", "pytest"], [python, "-m", "pytest"], ["python", "-m", "pytest"], ["python3", "-m", "pytest"]):
                return parts
            raise ValueError("CORTEX_VERIFY_COMMAND must be python -m pytest ...")
        if scope == "quick":
            preferred = ["tests/test_immune.py", "tests/test_deliberation.py", "tests/test_web.py"]
            existing = [p for p in preferred if (self.root / p).exists()]
            return [python, "-m", "pytest", "-q", *(existing or ["tests"])]
        return [python, "-m", "pytest", "-q"]

    def _python_with_pytest(self) -> str:
        candidates = [
            self.root / "cortex-mvp" / ".venv" / "bin" / "python",
            self.root / ".venv" / "bin" / "python",
            Path(sys.executable),
        ]
        for candidate in candidates:
            if not candidate.exists():
                continue
            proc = subprocess.run([str(candidate), "-m", "pytest", "--version"], cwd=self.root, text=True, capture_output=True, timeout=10)
            if proc.returncode == 0:
                return str(candidate)
        return sys.executable

    def _git_status(self) -> dict[str, Any]:
        if not (self.root / ".git").exists():
            return {"available": False, "short": ""}
        status = self._run_git(["status", "--short", "--branch"])
        return {"available": status["returncode"] == 0, "short": status["stdout"][:12000]}

    def _run_git(self, args: list[str]) -> dict[str, Any]:
        if not (self.root / ".git").exists():
            return {"returncode": 1, "stdout": "", "stderr": "not a git repo"}
        proc = subprocess.run(["git", *args], cwd=self.root, text=True, capture_output=True, timeout=30)
        return {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}

    def _persist(self, report: dict[str, Any]) -> None:
        (self.runtime / "latest.json").write_text(json.dumps(report, indent=2, sort_keys=True))

    def _append(self, stream: str, record: dict[str, Any]) -> None:
        with (self.ledger / stream).open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")
