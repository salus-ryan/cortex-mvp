"""Cortex Forge API server.

A small self-owned PaaS control plane for Cortex-style agents. It exposes
witness-gated deploy/check/rollback/log endpoints around allowlisted scripts.
"""

from __future__ import annotations

import argparse
import json
import os
import mimetypes
import subprocess
import threading
import urllib.request
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


class ForgeState:
    def __init__(self, root: Path, repo: Path, apps_path: Path | None = None) -> None:
        self.root = root.resolve()
        self.repo = repo.resolve()
        self.apps_path = apps_path
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
        body = json.dumps(job, indent=2, sort_keys=True)
        path = self.jobs / f"{job['id']}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(body)
        tmp.replace(path)
        latest = self.jobs / "latest.json"
        latest_tmp = self.jobs / "latest.json.tmp"
        latest_tmp.write_text(body)
        latest_tmp.replace(latest)
        return job

    def start_job(self, app: str, kind: str, witness: str | None, target, *args: Any) -> dict[str, Any]:
        job = {
            "id": "job_" + uuid.uuid4().hex[:12],
            "app": app,
            "type": kind,
            "status": "running",
            "started_at": self.now(),
            "finished_at": None,
            "witness": witness,
            "may_execute": False,
        }
        self.write_job(job)

        def runner() -> None:
            try:
                result = target(*args)
                job.update({"status": result.get("status", "finished"), "finished_at": self.now(), "result": result})
            except Exception as exc:
                job.update({"status": "failed", "finished_at": self.now(), "error": str(exc)})
            self.write_job(job)
            self.append("jobs.jsonl", {"action_type": "job_finished", **job})

        threading.Thread(target=runner, daemon=True).start()
        self.append("jobs.jsonl", {"action_type": "job_started", **job})
        return job

    def read_job(self, job_id: str = "latest") -> dict[str, Any]:
        path = self.jobs / ("latest.json" if job_id == "latest" else f"{job_id}.json")
        if not path.exists():
            return {"status": "none", "may_execute": False}
        return json.loads(path.read_text())

    def apps(self) -> dict[str, Any]:
        if self.apps_path and self.apps_path.exists():
            data = json.loads(self.apps_path.read_text())
            return data.get("apps", {})
        return {
            "cortex": {
                "repo": str(self.repo),
                "container": os.environ.get("CONTAINER_NAME", "cortex"),
                "image": os.environ.get("IMAGE_NAME", "cortex-mvp:forge"),
                "host_port": int(os.environ.get("HOST_PORT", "8080")),
                "port": int(os.environ.get("PORT", "8080")),
                "public_url": os.environ.get("PUBLIC_URL", "http://127.0.0.1:8080"),
                "data_root": os.environ.get("DATA_ROOT", "/var/lib/cortex"),
            }
        }

    def app(self, name: str) -> dict[str, Any] | None:
        return self.apps().get(name)

    def for_app(self, name: str) -> "ForgeState":
        cfg = self.app(name)
        if not cfg:
            raise KeyError(f"unknown app: {name}")
        return ForgeState(self.root / "apps" / name, Path(cfg["repo"]).resolve(), self.apps_path)

    def status(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "repo": str(self.repo),
            "root": str(self.root),
            "docker_available": self._cmd_available("docker"),
            "git_available": self._cmd_available("git"),
            "apps": self.apps(),
            "container": self.container_status(),
            "git": self.git_info(),
            "latest": self.read_job(),
            "may_execute": False,
        }

    def git_info(self) -> dict[str, Any]:
        head = self._run(["git", "rev-parse", "HEAD"], timeout=10)
        branch = self._run(["git", "rev-parse", "--abbrev-ref", "HEAD"], timeout=10)
        status = self._run(["git", "status", "--short", "--branch"], timeout=20)
        return {
            "head": head["stdout"].strip(),
            "branch": branch["stdout"].strip(),
            "status": status,
            "dirty": any(line and not line.startswith("##") for line in status["stdout"].splitlines()) if status["returncode"] == 0 else None,
        }

    def container_status(self, name: str | None = None) -> dict[str, Any]:
        name = name or os.environ.get("CONTAINER_NAME", "cortex")
        if not self._cmd_available("docker"):
            return {"available": False, "reason": "docker unavailable"}
        inspect = self._run(["docker", "inspect", name], timeout=20)
        if inspect["returncode"] != 0:
            return {"available": False, "name": name, "reason": inspect["stderr"] or inspect["stdout"]}
        try:
            data = json.loads(inspect["stdout"])[0]
        except Exception as exc:
            return {"available": False, "name": name, "reason": str(exc)}
        state = data.get("State", {})
        return {"available": True, "name": name, "running": bool(state.get("Running")), "status": state.get("Status"), "image": data.get("Config", {}).get("Image")}

    def container_logs(self, lines: int = 200, name: str | None = None) -> dict[str, Any]:
        name = name or os.environ.get("CONTAINER_NAME", "cortex")
        if not self._cmd_available("docker"):
            return {"status": "unavailable", "reason": "docker unavailable", "may_execute": False}
        lines = max(1, min(lines, 1000))
        proc = self._run(["docker", "logs", "--tail", str(lines), name], timeout=30)
        return {"status": "ok" if proc["returncode"] == 0 else "fail", "container": name, "stdout": proc["stdout"], "stderr": proc["stderr"], "may_execute": False}

    def health(self, public_url: str | None = None) -> dict[str, Any]:
        public_url = (public_url or os.environ.get("PUBLIC_URL") or "http://127.0.0.1:8080").rstrip("/")
        checks: list[dict[str, Any]] = []
        for path in ["/health", "/pid1"]:
            try:
                with urllib.request.urlopen(public_url + path, timeout=15) as resp:
                    data = json.loads(resp.read().decode())
                checks.append({"path": path, "status": "pass", "code": resp.status, "data": data})
            except Exception as exc:
                checks.append({"path": path, "status": "fail", "error": str(exc)})
        ok = all(c["status"] == "pass" for c in checks)
        return {"status": "pass" if ok else "fail", "public_url": public_url, "checks": checks, "may_execute": False}

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

    def update_repo(self, witness: str | None, confirmed: bool, expected_branch: str | None = None) -> dict[str, Any]:
        if not witness:
            return self._refuse("repo update requires witness")
        if not confirmed:
            return self._refuse("repo update requires confirmed=true")
        if expected_branch:
            branch = self._run(["git", "rev-parse", "--abbrev-ref", "HEAD"], timeout=10)["stdout"].strip()
            if branch != expected_branch:
                return self._refuse("branch mismatch", {"expected": expected_branch, "actual": branch})
        proc = subprocess.run(["git", "pull", "--ff-only"], cwd=self.repo, text=True, capture_output=True, timeout=120)
        job = {
            "id": "update_" + uuid.uuid4().hex[:12],
            "status": "updated" if proc.returncode == 0 else "failed",
            "timestamp": self.now(),
            "command": ["git", "pull", "--ff-only"],
            "returncode": proc.returncode,
            "stdout": proc.stdout[-12000:],
            "stderr": proc.stderr[-12000:],
            "witness": witness,
            "confirmed": confirmed,
            "may_execute": False,
        }
        self.write_job(job)
        self.append("forge.jsonl", {"action_type": "update_repo", **job})
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

        def _file(self, path: Path) -> None:
            if not path.exists() or not path.is_file():
                self._json(404, {"status": "not_found"})
                return
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("content-type", mimetypes.guess_type(str(path))[0] or "application/octet-stream")
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
            if self.path in {"/ui", "/dashboard"}:
                self._file(Path(__file__).parent / "static" / "index.html")
            elif self.path.startswith("/static/"):
                rel = self.path.removeprefix("/static/").split("?", 1)[0]
                self._file(Path(__file__).parent / "static" / rel)
            elif self.path in {"/", "/forge/status"}:
                self._json(200, state.status())
            elif self.path == "/forge/apps":
                self._json(200, {"status": "ok", "apps": state.apps(), "may_execute": False})
            elif self.path.startswith("/forge/apps/"):
                parts = self.path.split("?")[0].strip("/").split("/")
                if len(parts) < 3:
                    self._json(404, {"status": "not_found"}); return
                app_name = parts[2]
                action = parts[3] if len(parts) > 3 else "status"
                try:
                    app_state = state.for_app(app_name)
                except KeyError as exc:
                    self._json(404, {"status": "not_found", "reason": str(exc)}); return
                if action == "status":
                    self._json(200, app_state.status())
                elif action == "check":
                    self._json(200, app_state.check())
                elif action == "logs":
                    self._json(200, app_state.container_logs())
                elif action == "health":
                    self._json(200, app_state.health(app_state.app(app_name).get("public_url") if app_state.app(app_name) else None))
                else:
                    self._json(404, {"status": "not_found"})
            elif self.path == "/forge/check":
                self._json(200, state.check())
            elif self.path.startswith("/forge/job") or self.path.startswith("/forge/jobs/"):
                if self.path.startswith("/forge/jobs/"):
                    job_id = self.path.rsplit("/", 1)[-1]
                else:
                    _, _, query = self.path.partition("?")
                    job_id = "latest"
                    if query.startswith("id="):
                        job_id = query.removeprefix("id=")
                self._json(200, state.read_job(job_id))
            elif self.path.startswith("/forge/logs"):
                _, _, query = self.path.partition("?")
                lines = 200
                if query.startswith("lines="):
                    try:
                        lines = int(query.removeprefix("lines="))
                    except ValueError:
                        lines = 200
                self._json(200, state.container_logs(lines))
            elif self.path.startswith("/forge/health"):
                self._json(200, state.health())
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
            if self.path.startswith("/forge/apps/"):
                parts = self.path.strip("/").split("/")
                if len(parts) < 4:
                    self._json(404, {"status": "not_found"}); return
                app_name, action = parts[2], parts[3]
                try:
                    app_state = state.for_app(app_name)
                except KeyError as exc:
                    self._json(404, {"status": "not_found", "reason": str(exc)}); return
                cfg = state.app(app_name) or {}
                if action == "deploy":
                    if body.get("async", True):
                        job = state.start_job(app_name, "deploy", body.get("witness"), app_state.deploy, body.get("witness"), bool(body.get("confirmed", False)), body.get("public_url", cfg.get("public_url")))
                        self._json(202, job)
                    else:
                        result = app_state.deploy(body.get("witness"), bool(body.get("confirmed", False)), body.get("public_url", cfg.get("public_url")))
                        self._json(200 if result["status"] == "deployed" else 403, result)
                elif action == "update":
                    job = state.start_job(app_name, "update", body.get("witness"), app_state.update_repo, body.get("witness"), bool(body.get("confirmed", False)), body.get("expected_branch"))
                    self._json(202, job)
                elif action == "rollback":
                    job = state.start_job(app_name, "rollback", body.get("witness"), app_state.rollback, body.get("witness"), bool(body.get("confirmed", False)))
                    self._json(202, job)
                else:
                    self._json(404, {"status": "not_found"})
            elif self.path == "/forge/deploy":
                result = state.deploy(body.get("witness"), bool(body.get("confirmed", False)), body.get("public_url"))
                self._json(200 if result["status"] == "deployed" else 403, result)
            elif self.path == "/forge/rollback":
                result = state.rollback(body.get("witness"), bool(body.get("confirmed", False)))
                self._json(200 if result["status"] == "rolled_back" else 403, result)
            elif self.path == "/forge/update":
                result = state.update_repo(body.get("witness"), bool(body.get("confirmed", False)), body.get("expected_branch"))
                self._json(200 if result["status"] == "updated" else 403, result)
            else:
                self._json(404, {"status": "not_found"})

        def log_message(self, fmt: str, *args: Any) -> None:
            print("cortex-forge", self.address_string(), fmt % args, flush=True)

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=os.environ.get("FORGE_ROOT", "/var/lib/cortex-forge"))
    parser.add_argument("--repo", default=os.environ.get("FORGE_REPO", os.getcwd()))
    parser.add_argument("--apps", default=os.environ.get("FORGE_APPS"))
    parser.add_argument("--host", default=os.environ.get("FORGE_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("FORGE_PORT", "8765")))
    args = parser.parse_args(argv)
    state = ForgeState(Path(args.root), Path(args.repo), Path(args.apps) if args.apps else None)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(state, os.environ.get("FORGE_TOKEN")))
    print(f"cortex forge serving on {args.host}:{args.port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
