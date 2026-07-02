"""Small localhost IPC clients with in-process fallback."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from cortex.oracle import OracleService
from cortex.services import GuardianService, ScribeService

PORTS = {"guardian": 8101, "scribe": 8102, "oracle": 8103, "prophet": 8104}


def post(role: str, path: str, payload: dict[str, Any], timeout: float = 2.0) -> dict[str, Any]:
    url = f"http://127.0.0.1:{PORTS[role]}{path}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get(role: str, path: str, timeout: float = 2.0) -> dict[str, Any]:
    with urllib.request.urlopen(f"http://127.0.0.1:{PORTS[role]}{path}", timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class GuardianClient:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def check_invocation(self, authority: str, tools: list[str], confirmed: bool = False) -> dict[str, Any]:
        try:
            return post("guardian", "/check", {"authority": authority, "tools": tools, "confirmed": confirmed})
        except Exception:
            result = GuardianService(self.root).check_invocation(authority, tools, confirmed)
            return {"allowed": result.allowed, "reason": result.reason, "law": result.law, "fallback": "in_process"}


class ScribeClient:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def append(self, stream: str, record: dict[str, Any]) -> dict[str, Any]:
        try:
            return post("scribe", "/append", {"stream": stream, "record": record})["record"]
        except Exception:
            rec = ScribeService(self.root).append(stream, record)
            rec["fallback"] = "in_process"
            return rec

    def read_tail(self, stream: str) -> list[dict[str, Any]]:
        try:
            return get("scribe", f"/tail/{stream}")["records"]
        except Exception:
            return ScribeService(self.root).read_tail(stream)


class OracleClient:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def propose(self, task: str, authority: str, context: dict[str, Any]) -> dict[str, Any]:
        try:
            return post("oracle", "/propose", {"task": task, "authority": authority, "context": context}, timeout=50.0)
        except Exception:
            data = OracleService(self.root).propose(task, authority, context).to_dict()
            data["fallback"] = "in_process"
            return data


class ProphetClient:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def evaluate(self) -> dict[str, Any]:
        try:
            return post("prophet", "/evaluate", {}, timeout=60.0)
        except Exception:
            from cortex.prophet import ProphetService
            data = ProphetService(self.root).evaluate()
            data["fallback"] = "in_process"
            return data

    def report(self) -> dict[str, Any]:
        try:
            return get("prophet", "/report", timeout=10.0)
        except Exception:
            from cortex.prophet import ProphetService
            data = ProphetService(self.root).latest()
            data["fallback"] = "in_process"
            return data
