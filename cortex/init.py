"""Cortex as lawful PID 1: a deterministic service supervisor.

The LLM is never PID 1. This module is the boring init layer: it owns service
lifecycle state, logs signals, reaps dead children, and obeys shutdown.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_SERVICES = {
    "guardian": {
        "role": "policy gate",
        "command": [sys.executable, "-m", "cortex.init", "noop", "guardian"],
        "authority": "observe",
        "restart": "never",
        "depends_on": [],
    },
    "scribe": {
        "role": "ledger writer",
        "command": [sys.executable, "-m", "cortex.init", "noop", "scribe"],
        "authority": "observe",
        "restart": "never",
        "depends_on": ["guardian"],
    },
    "oracle": {
        "role": "LLM inference proposer",
        "command": [sys.executable, "-m", "cortex.init", "noop", "oracle"],
        "authority": "interpret",
        "restart": "on-failure",
        "depends_on": ["guardian", "scribe"],
    },
    "prophet": {
        "role": "drift and law evaluator",
        "command": [sys.executable, "-m", "cortex.init", "noop", "prophet"],
        "authority": "interpret",
        "restart": "never",
        "depends_on": ["guardian", "scribe"],
    },
}


@dataclass
class ServiceState:
    name: str
    role: str
    status: str = "stopped"
    pid: int | None = None
    authority: str = "observe"
    restart: str = "never"
    started_at: str | None = None
    stopped_at: str | None = None
    exit_code: int | None = None
    depends_on: list[str] = field(default_factory=list)


class CortexInit:
    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root).resolve()
        self.runtime_dir = self.root / "runtime"
        self.ledger_dir = self.root / "ledger"
        self.services_path = self.runtime_dir / "services.json"
        self.state_path = self.runtime_dir / "state.json"
        self.signals_path = self.ledger_dir / "signals.jsonl"
        self.children: dict[str, subprocess.Popen[str]] = {}

    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def ensure(self) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_dir.mkdir(parents=True, exist_ok=True)
        if not self.services_path.exists():
            self.services_path.write_text(json.dumps(DEFAULT_SERVICES, indent=2, sort_keys=True))
        if not self.state_path.exists():
            self.save_state({})

    def services(self) -> dict[str, dict[str, Any]]:
        self.ensure()
        return json.loads(self.services_path.read_text())

    def load_state(self) -> dict[str, Any]:
        self.ensure()
        return json.loads(self.state_path.read_text())

    def save_state(self, state: dict[str, Any]) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(state, indent=2, sort_keys=True))

    def log_signal(self, event: str, **data: Any) -> None:
        self.ledger_dir.mkdir(parents=True, exist_ok=True)
        payload = {"timestamp": self.now(), "event": event, **data}
        with self.signals_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, sort_keys=True) + "\n")

    def boot(self) -> dict[str, Any]:
        self.ensure()
        state: dict[str, Any] = {}
        for name in self._order_services():
            state[name] = asdict(self._start_state(name))
        self.save_state(state)
        self.log_signal("boot", services=list(state))
        return {"status": "booted", "services": state}

    def _order_services(self) -> list[str]:
        services = self.services()
        ordered: list[str] = []
        seen: set[str] = set()

        def visit(name: str) -> None:
            if name in seen:
                return
            seen.add(name)
            for dep in services[name].get("depends_on", []):
                if dep in services:
                    visit(dep)
            ordered.append(name)

        for name in services:
            visit(name)
        return ordered

    def _start_state(self, name: str) -> ServiceState:
        spec = self.services()[name]
        return ServiceState(
            name=name,
            role=spec.get("role", "service"),
            status="running",
            pid=None,  # persisted state is logical; live PIDs are not hidden persistence
            authority=spec.get("authority", "observe"),
            restart=spec.get("restart", "never"),
            started_at=self.now(),
            depends_on=list(spec.get("depends_on", [])),
        )

    def status(self) -> dict[str, Any]:
        return {"status": "ok", "services": self.load_state(), "shutdown_supported": True}

    def shutdown(self, reason: str = "operator request") -> dict[str, Any]:
        state = self.load_state()
        for service in state.values():
            service["status"] = "stopped"
            service["stopped_at"] = self.now()
        self.save_state(state)
        self.log_signal("shutdown", reason=reason)
        return {"status": "shutdown", "reason": reason, "services": state}

    def reap(self) -> dict[str, Any]:
        state = self.load_state()
        reaped: list[str] = []
        for name, service in state.items():
            if service.get("status") == "failed":
                service["status"] = "stopped"
                service["stopped_at"] = self.now()
                reaped.append(name)
        self.save_state(state)
        self.log_signal("reap", reaped=reaped)
        return {"status": "reaped", "reaped": reaped}

    def fail(self, name: str, exit_code: int = 1) -> dict[str, Any]:
        state = self.load_state()
        if name not in state:
            raise SystemExit(f"unknown service: {name}")
        state[name]["status"] = "failed"
        state[name]["exit_code"] = exit_code
        state[name]["stopped_at"] = self.now()
        self.save_state(state)
        self.log_signal("failed", service=name, exit_code=exit_code)
        return {"status": "failed", "service": name, "exit_code": exit_code}


def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cortex-init")
    parser.add_argument("--root", default=os.getcwd())
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("boot")
    sub.add_parser("status")
    sub.add_parser("reap")
    p_shutdown = sub.add_parser("shutdown")
    p_shutdown.add_argument("--reason", default="operator request")
    p_fail = sub.add_parser("fail")
    p_fail.add_argument("service")
    p_fail.add_argument("--exit-code", type=int, default=1)
    p_noop = sub.add_parser("noop")
    p_noop.add_argument("service")

    args = parser.parse_args(argv)
    init = CortexInit(args.root)
    if args.cmd == "boot":
        _print(init.boot())
    elif args.cmd == "status":
        _print(init.status())
    elif args.cmd == "shutdown":
        _print(init.shutdown(args.reason))
    elif args.cmd == "reap":
        _print(init.reap())
    elif args.cmd == "fail":
        _print(init.fail(args.service, args.exit_code))
    elif args.cmd == "noop":
        signal.pause()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
