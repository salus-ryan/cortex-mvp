"""Literal-ish Cortex PID 1 supervisor for containers.

When used as Docker ENTRYPOINT, this process becomes PID 1. It starts child
services, handles termination signals, reaps exited children, applies bounded
restart policy, logs lifecycle events, and exits rather than hiding persistence.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(os.environ.get("CORTEX_ROOT", os.getcwd())).resolve()
RUNTIME = ROOT / "runtime"
LEDGER = ROOT / "ledger"
PID1_STATUS = RUNTIME / "pid1.json"
PID1_SIGNALS = LEDGER / "pid1-signals.jsonl"


@dataclass
class ChildSpec:
    name: str
    command: list[str]
    restart: str = "never"  # never | on-failure
    authority: str = "observe"
    max_restarts: int = 3


@dataclass
class ChildState:
    name: str
    command: list[str]
    restart: str
    authority: str
    pid: int | None = None
    status: str = "stopped"
    starts: int = 0
    restarts: int = 0
    exit_code: int | None = None
    started_at: str | None = None
    stopped_at: str | None = None


DEFAULT_CHILDREN = [
    ChildSpec(
        name="web",
        command=[sys.executable, "-m", "cortex.web"],
        restart="on-failure",
        authority="observe",
    ),
    ChildSpec(
        name="guardian",
        command=[sys.executable, "-m", "cortex.service_server", "guardian"],
        restart="on-failure",
        authority="observe",
    ),
    ChildSpec(
        name="scribe",
        command=[sys.executable, "-m", "cortex.service_server", "scribe"],
        restart="on-failure",
        authority="observe",
    ),
    ChildSpec(
        name="oracle",
        command=[sys.executable, "-m", "cortex.service_server", "oracle"],
        restart="on-failure",
        authority="interpret",
    ),
    ChildSpec(
        name="prophet",
        command=[sys.executable, "-m", "cortex.service_server", "prophet"],
        restart="on-failure",
        authority="interpret",
    ),
    ChildSpec(
        name="memory",
        command=[sys.executable, "-m", "cortex.service_server", "memory"],
        restart="on-failure",
        authority="observe",
    ),
    ChildSpec(
        name="tool",
        command=[sys.executable, "-m", "cortex.service_server", "tool"],
        restart="on-failure",
        authority="observe",
    ),
    ChildSpec(
        name="planner",
        command=[sys.executable, "-m", "cortex.service_server", "planner"],
        restart="on-failure",
        authority="prepare",
    ),
    ChildSpec(
        name="deliberator",
        command=[sys.executable, "-m", "cortex.service_server", "deliberator"],
        restart="on-failure",
        authority="interpret",
    ),
    ChildSpec(
        name="immune",
        command=[sys.executable, "-m", "cortex.service_server", "immune"],
        restart="on-failure",
        authority="interpret",
    ),
    ChildSpec(
        name="repo",
        command=[sys.executable, "-m", "cortex.service_server", "repo"],
        restart="on-failure",
        authority="act_reversible",
    ),
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_dirs() -> None:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    LEDGER.mkdir(parents=True, exist_ok=True)


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    ensure_dirs()
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, sort_keys=True) + "\n")


class CortexPID1:
    def __init__(self, specs: list[ChildSpec] | None = None) -> None:
        self.specs = DEFAULT_CHILDREN if specs is None else specs
        self.children: dict[int, subprocess.Popen[str]] = {}
        self.states: dict[str, ChildState] = {
            spec.name: ChildState(spec.name, spec.command, spec.restart, spec.authority)
            for spec in self.specs
        }
        self.shutting_down = False
        self.received: list[str] = []

    @property
    def is_pid1(self) -> bool:
        return os.getpid() == 1

    def log(self, event: str, **data: Any) -> None:
        payload = {"timestamp": now(), "event": event, "pid": os.getpid(), **data}
        append_jsonl(PID1_SIGNALS, payload)
        print(json.dumps(payload, sort_keys=True), flush=True)

    def write_status(self) -> None:
        ensure_dirs()
        payload = {
            "timestamp": now(),
            "pid": os.getpid(),
            "is_pid1": self.is_pid1,
            "shutting_down": self.shutting_down,
            "received_signals": self.received,
            "children": {name: asdict(state) for name, state in self.states.items()},
        }
        PID1_STATUS.write_text(json.dumps(payload, indent=2, sort_keys=True))

    def install_signal_handlers(self) -> None:
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(sig, self._handle_signal)
        # SIGCHLD wakes the loop on Unix; reaping is still done explicitly.
        if hasattr(signal, "SIGCHLD"):
            signal.signal(signal.SIGCHLD, lambda _s, _f: None)

    def _handle_signal(self, sig: int, _frame: Any) -> None:
        name = signal.Signals(sig).name
        self.received.append(name)
        self.log("signal", signal=name)
        if sig in (signal.SIGTERM, signal.SIGINT):
            self.shutting_down = True

    def start_child(self, spec: ChildSpec) -> None:
        state = self.states[spec.name]
        proc = subprocess.Popen(spec.command, cwd=ROOT, text=True)
        self.children[proc.pid] = proc
        state.pid = proc.pid
        state.status = "running"
        state.starts += 1
        state.started_at = now()
        state.stopped_at = None
        state.exit_code = None
        self.log("child_started", child=spec.name, child_pid=proc.pid, authority=spec.authority)
        self.write_status()

    def start_all(self) -> None:
        for spec in self.specs:
            self.start_child(spec)

    def reap(self) -> None:
        for pid, proc in list(self.children.items()):
            code = proc.poll()
            if code is None:
                continue
            del self.children[pid]
            state = next((s for s in self.states.values() if s.pid == pid), None)
            if state is None:
                self.log("unknown_child_reaped", child_pid=pid, exit_code=code)
                continue
            state.status = "exited" if code == 0 else "failed"
            state.exit_code = code
            state.stopped_at = now()
            self.log("child_reaped", child=state.name, child_pid=pid, exit_code=code)
            self._maybe_restart(state)
        self.write_status()

    def _maybe_restart(self, state: ChildState) -> None:
        if self.shutting_down or state.exit_code == 0 or state.restart != "on-failure":
            return
        spec = next(s for s in self.specs if s.name == state.name)
        if state.restarts >= spec.max_restarts:
            self.log("restart_refused", child=state.name, reason="max_restarts_exceeded")
            return
        state.restarts += 1
        self.log("child_restart", child=state.name, restart_count=state.restarts)
        self.start_child(spec)

    def terminate_children(self, grace_seconds: float = 10.0) -> None:
        self.log("shutdown_begin", children=list(self.children))
        for proc in list(self.children.values()):
            if proc.poll() is None:
                proc.terminate()
        deadline = time.time() + grace_seconds
        while time.time() < deadline and any(p.poll() is None for p in self.children.values()):
            self.reap()
            time.sleep(0.2)
        for proc in list(self.children.values()):
            if proc.poll() is None:
                self.log("child_kill", child_pid=proc.pid)
                proc.kill()
        self.reap()
        self.log("shutdown_complete")

    def run(self) -> int:
        ensure_dirs()
        self.install_signal_handlers()
        self.log("pid1_start", is_pid1=self.is_pid1, root=str(ROOT))
        self.start_all()
        try:
            while not self.shutting_down:
                self.reap()
                # If the web service is gone and not restarted, fail the container.
                web = self.states.get("web")
                if web and web.status in {"failed", "exited"} and web.pid not in self.children:
                    self.log("pid1_failure", reason="web_not_running", web_status=web.status)
                    self.shutting_down = True
                    break
                time.sleep(1.0)
        finally:
            self.terminate_children()
        return 0


def child(name: str) -> int:
    def handler(sig: int, _frame: Any) -> None:
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)
    append_jsonl(PID1_SIGNALS, {"timestamp": now(), "event": "role_child_ready", "role": name, "pid": os.getpid()})
    while True:
        time.sleep(60)


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if argv[:1] == ["child"]:
        return child(argv[1] if len(argv) > 1 else "unnamed")
    return CortexPID1().run()


if __name__ == "__main__":
    raise SystemExit(main())
