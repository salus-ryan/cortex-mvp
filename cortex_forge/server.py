"""Cortex Forge API server.

A small self-owned PaaS control plane for Cortex-style agents. It exposes
witness-gated deploy/check/rollback/log endpoints around allowlisted scripts.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


class ForgeState:
    def __init__(self, root: Path, repo: Path) -> None:
        self.root = root.resolve()
        self.repo = repo.resolve()
        self.jobs = self.root / "jobs"
        self.ledger = self.root / "ledger"
        self.logs = self.root / "logs"
        for path in [self.jobs, self.ledger, self.logs]:
            path.mkdir(parents=True, exist_ok=True)

    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def append(self, stream: str, record: dict[str, Any]) -> None:
        with (self.ledger / stream).open("a", encoding="utf-8") as f:
            f.write(json.dumps({"timestamp": self.now(), **record}, sort_keys=True) + "\n")

    def write_job(self, job: dict[str, Any]) -> dict[str, Any]:
        (self.jobs / f"{job['id']}.json").write_text(json.dumps(job, indent=2, sort_keys=True))
        (self.jobs / "latest.json").write_text(json.dumps(job, indent=2, sort_keys=True))
        return job

    def read_job(self, job_id: str = "latest") -> dict[str, Any]:
        path = self.jobs / ("latest.json" if job_id == "latest" else f"{job_id}.json")
        if not path.exists():
            return {"status": "none", "may_execute": False}
        return json.loads(path.read_text())

    def status(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "repo": str(self.repo),
            "root": str(self.root),
            "docker_available": self._cmd_available("docker"),
            "git_available": self._cmd_available("git"),
            "latest": self.read_job(),
            "may_execute": False,
        }

    def check(self) -> dict[str, Any]:
        blockers: list[str] = []
        if not self._cmd_available("docker"):
            blockers.append("docker unavailable")
        if not (self.repo / "Dockerfile").exists():
            blockers.append("Dockerfile missing")
        if not (self.repo / "forge" / "deploy.sh").exists():
            blockers.append("forge/deploy.sh missing")
        if not (self.repo / "forge" / "healthcheck.sh").exists():
            blockers.append("forge/healthcheck.sh missing")
        git = self._run(["git", "status", "--short", "--branch"], timeout=20)
        dirty = any(line and not line.startswith("##") for line in git["stdout"].splitlines()) if git["returncode"] == 0 else False
        if dirty:
            blockers.append("git workspace dirty")
        result = {
            "status": "pass" if not blockers else "blocked",
            "blockers": blockers,
            "git": git,
            "may_execute": False,
            "timestamp": self.now(),
        }
        self.append("forge.jsonl", {"action_type": "check", **result})
        return result

    def deploy(self, witness: str | None, confirmed: bool, public_url: str | None = None) -> dict[str, Any]:
        if not witness:
            return self._refuse("deploy requires witness")
        if not confirmed:
            return self._refuse("deploy requires confirmed=true")
        preflight = self.check()
        if preflight["status"] != "pass":
            return self._refuse("preflight blocked", preflight)
        job_id = "forge_" + uuid.uuid4().hex[:12]
        env = os.environ.copy()
        env["WITNESS"] = witness
        env["CONFIRMED"] = "true"
        if public_url:
            env["PUBLIC_URL"] = public_url
        proc = subprocess.run([str(self.repo / "forge" / "deploy.sh")], cwd=self.repo, env=env, text=True, capture_output=True, timeout=900)
        job = {
            "id": job_id,
            "status": "deployed" if proc.returncode == 0 else "failed",
            "timestamp": self.now(),
            "command": ["forge/deploy.sh"],
            "returncode": proc.returncode,
            "stdout": proc.stdout[-12000:],
            "stderr": proc.stderr[-12000:],
            "witness": witness,
            "confirmed": confirmed,
            "public_url": public_url,
            "may_execute": False,
        }
        self.write_job(job)
        self.append("forge.jsonl", {"action_type": "deploy", **job})
        return job

    def rollback(self, witness: str | None, confirmed: bool) -> dict[str, Any]:
        if not witness:
            return self._refuse("rollback requires witness")
        if not confirmed:
            return self._refuse("rollback requires confirmed=true")
        script = self.repo / "forge" / "rollback.sh"
        if not script.exists():
            return self._refuse("rollback script unavailable")
        proc = subprocess.run([str(script)], cwd=self.repo, text=True, capture_output=True, timeout=180)
        job = {
            "id": "rollback_" + uuid.uuid4().hex[:12],
            "status": "rolled_back" if proc.returncode == 0 else "failed",
            "timestamp": self.now(),
            "command": ["forge/rollback.sh"],
            "returncode": proc.returncode,
            "stdout": proc.stdout[-12000:],
            "stderr": proc.stderr[-12000:],
            "witness": witness,
            "confirmed": confirmed,
            "may_execute": False,
        }
        self.write_job(job)
        self.append("forge.jsonl", {"action_type": "rollback", **job})
        return job

    def _refuse(self, reason: str, detail: dict[str, Any] | None = None) -> dict[str, Any]:
        rec = {"status": "refused", "reason": reason, "detail": detail or {}, "may_execute": False, "timestamp": self.now()}
        self.append("forge.jsonl", {"action_type": "refuse", **rec})
        return rec

    def _cmd_available(self, command: str) -> bool:
        return subprocess.run(["sh", "-lc", f"command -v {command}"], text=True, capture_output=True).returncode == 0

    def _run(self, cmd: list[str], timeout: int = 30) -> dict[str, Any]:
        try:
            proc = subprocess.run(cmd, cwd=self.repo, text=True, capture_output=True, timeout=timeout)
            return {"returncode": proc.returncode, "stdout": proc.stdout[-8000:], "stderr": proc.stderr[-4000:]}
        except Exception as exc:
            return {"returncode": 1, "stdout": "", "stderr": str(exc)}


def make_handler(state: ForgeState, token: str | None):
    class Handler(BaseHTTPRequestHandler):
        def _json(self, code: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, indent=2, sort_keys=True).encode()
            self.send_response(code)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("content-length", "0") or "0")
            if not length:
                return {}
            raw = self.rfile.read(length).decode()
            return json.loads(raw) if raw.strip() else {}

        def _authorized(self) -> bool:
            if not token:
                return True
            return self.headers.get("authorization") == f"Bearer {token}"

        def do_GET(self) -> None:  # noqa: N802
            if self.path in {"/", "/forge/status"}:
                self._json(200, state.status())
            elif self.path == "/forge/check":
                self._json(200, state.check())
            elif self.path.startswith("/forge/job"):
                _, _, query = self.path.partition("?")
                job_id = "latest"
                if query.startswith("id="):
                    job_id = query.removeprefix("id=")
                self._json(200, state.read_job(job_id))
            else:
                self._json(404, {"status": "not_found"})

        def do_POST(self) -> None:  # noqa: N802
            if not self._authorized():
                self._json(401, {"status": "unauthorized"})
                return
            try:
                body = self._body()
            except json.JSONDecodeError as exc:
                self._json(400, {"status": "bad_json", "error": str(exc)})
                return
            if self.path == "/forge/deploy":
                result = state.deploy(body.get("witness"), bool(body.get("confirmed", False)), body.get("public_url"))
                self._json(200 if result["status"] == "deployed" else 403, result)
            elif self.path == "/forge/rollback":
                result = state.rollback(body.get("witness"), bool(body.get("confirmed", False)))
                self._json(200 if result["status"] == "rolled_back" else 403, result)
            else:
                self._json(404, {"status": "not_found"})

        def log_message(self, fmt: str, *args: Any) -> None:
            print("cortex-forge", self.address_string(), fmt % args, flush=True)

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=os.environ.get("FORGE_ROOT", "/var/lib/cortex-forge"))
    parser.add_argument("--repo", default=os.environ.get("FORGE_REPO", os.getcwd()))
    parser.add_argument("--host", default=os.environ.get("FORGE_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("FORGE_PORT", "8765")))
    args = parser.parse_args(argv)
    state = ForgeState(Path(args.root), Path(args.repo))
    server = ThreadingHTTPServer((args.host, args.port), make_handler(state, os.environ.get("FORGE_TOKEN")))
    print(f"cortex forge serving on {args.host}:{args.port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
