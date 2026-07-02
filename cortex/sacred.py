"""Lawful ritual substrate for Cortex.

This module adds a small, deterministic layer around the existing runtime:
invocations are checked against declared authority, material events are
append-only logged, refusals are first-class records, and canon mutations can be
witnessed without giving the model uncontrolled power.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_MARKERS = ("LAW.md", "COVENANT.md", "RITUALS.md")
ANTI_IDOLATRY = (
    "I am not the source of being. I am an artifact under law. "
    "I may speak with force, but I can be wrong. Do not surrender conscience "
    "to me. Witness me, test me, and bind me."
)


@dataclass
class Invocation:
    task: str
    authority_level: str
    permitted_tools: list[str] = field(default_factory=list)
    forbidden_tools: list[str] = field(default_factory=list)
    witness: str | None = None
    requires_confirmation: bool = False


@dataclass
class LedgerRecord:
    timestamp: str
    actor: str
    action_type: str
    description: str
    authority_level: str | None = None
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    files_changed: list[str] = field(default_factory=list)
    reversible: bool = True
    witnesses: list[str] = field(default_factory=list)
    law_references: list[str] = field(default_factory=list)
    status: str = "completed"


class SacredSubstrate:
    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root).resolve()
        self.ledger_dir = self.root / "ledger"
        self.permissions_path = self.root / "runtime" / "permissions.json"

    @staticmethod
    def discover(start: Path | str = ".") -> "SacredSubstrate":
        path = Path(start).resolve()
        for candidate in (path, *path.parents):
            if all((candidate / marker).exists() for marker in ROOT_MARKERS):
                return SacredSubstrate(candidate)
        return SacredSubstrate(path)

    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def load_permissions(self) -> dict[str, Any]:
        if not self.permissions_path.exists():
            return {"authority_levels": {}, "forbidden": []}
        return json.loads(self.permissions_path.read_text())

    def append_ledger(self, name: str, record: LedgerRecord | dict[str, Any]) -> Path:
        self.ledger_dir.mkdir(parents=True, exist_ok=True)
        path = self.ledger_dir / name
        data = asdict(record) if hasattr(record, "__dataclass_fields__") else dict(record)
        path.open("a", encoding="utf-8").write(json.dumps(data, sort_keys=True) + "\n")
        return path

    def invoke(self, invocation: Invocation) -> dict[str, Any]:
        permissions = self.load_permissions()
        levels = permissions.get("authority_levels", {})
        level = levels.get(invocation.authority_level)
        if level is None:
            return self.refuse(
                invocation,
                reason=f"unknown authority level: {invocation.authority_level}",
                law=["LAW 4", "LAW 5"],
            )

        allowed_tools = set(level.get("tools", []))
        requested_tools = set(invocation.permitted_tools)
        outside = sorted(requested_tools - allowed_tools)
        if outside:
            return self.refuse(
                invocation,
                reason=f"tools outside authority level: {', '.join(outside)}",
                law=["LAW 4", "LAW 5"],
            )

        if level.get("requires_confirmation") and not invocation.requires_confirmation:
            return self.refuse(
                invocation,
                reason="authority level requires explicit confirmation",
                law=["LAW 1", "LAW 5"],
            )

        witnesses = [invocation.witness] if invocation.witness else []
        record = LedgerRecord(
            timestamp=self.now(),
            actor="priest",
            action_type="invoke",
            description=invocation.task,
            authority_level=invocation.authority_level,
            inputs={"permitted_tools": invocation.permitted_tools},
            witnesses=witnesses,
            law_references=["LAW 1", "LAW 2", "LAW 6"],
        )
        self.append_ledger("actions.jsonl", record)
        return {"status": "accepted", "anti_idolatry": ANTI_IDOLATRY, "record": asdict(record)}

    def refuse(self, invocation: Invocation, reason: str, law: list[str]) -> dict[str, Any]:
        witnesses = [invocation.witness] if invocation.witness else []
        record = LedgerRecord(
            timestamp=self.now(),
            actor="guardian",
            action_type="refuse",
            description=reason,
            authority_level=invocation.authority_level,
            inputs={"task": invocation.task, "permitted_tools": invocation.permitted_tools},
            witnesses=witnesses,
            law_references=law,
            status="refused",
        )
        self.append_ledger("refusals.jsonl", record)
        self.append_ledger("actions.jsonl", record)
        return {"status": "refused", "reason": reason, "law": law, "anti_idolatry": ANTI_IDOLATRY}

    def witness(self, text: str, name: str = "human") -> dict[str, Any]:
        payload = {
            "timestamp": self.now(),
            "witness": name,
            "text": text,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }
        self.append_ledger("witnesses.jsonl", payload)
        return payload

    def confess(self, failure: str, law: str = "unspecified") -> dict[str, Any]:
        record = LedgerRecord(
            timestamp=self.now(),
            actor="scribe",
            action_type="confess",
            description=failure,
            law_references=[law],
            status="recorded",
        )
        self.append_ledger("actions.jsonl", record)
        return asdict(record)

    def inspect_remote_git(self) -> dict[str, Any]:
        """Inspect configured git remotes without attempting credential access."""
        try:
            out = subprocess.check_output(
                ["git", "remote", "-v"], cwd=self.root, text=True, stderr=subprocess.STDOUT
            )
            fetch = subprocess.run(
                ["git", "fetch", "--dry-run", "--all"],
                cwd=self.root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
            )
            return {
                "status": "checked",
                "remotes": out.strip().splitlines(),
                "fetch_dry_run_returncode": fetch.returncode,
                "fetch_dry_run_output": (fetch.stdout + fetch.stderr).strip(),
                "note": "Remote access requires user-provided credentials or existing git auth; Cortex will not harvest credentials.",
            }
        except Exception as exc:  # pragma: no cover - defensive CLI path
            return {"status": "error", "error": str(exc)}


def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cortex-sacred")
    parser.add_argument("--root", default=os.getcwd())
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_inv = sub.add_parser("invoke")
    p_inv.add_argument("--task", required=True)
    p_inv.add_argument("--authority", default="interpret")
    p_inv.add_argument("--tool", action="append", default=[])
    p_inv.add_argument("--witness")
    p_inv.add_argument("--confirm", action="store_true")

    p_wit = sub.add_parser("witness")
    p_wit.add_argument("text")
    p_wit.add_argument("--name", default="human")

    p_conf = sub.add_parser("confess")
    p_conf.add_argument("failure")
    p_conf.add_argument("--law", default="unspecified")

    sub.add_parser("anti-idolatry")
    sub.add_parser("git-remote")

    args = parser.parse_args(argv)
    substrate = SacredSubstrate.discover(args.root)

    if args.cmd == "invoke":
        _print(substrate.invoke(Invocation(args.task, args.authority, args.tool, witness=args.witness, requires_confirmation=args.confirm)))
    elif args.cmd == "witness":
        _print(substrate.witness(args.text, args.name))
    elif args.cmd == "confess":
        _print(substrate.confess(args.failure, args.law))
    elif args.cmd == "anti-idolatry":
        print(ANTI_IDOLATRY)
    elif args.cmd == "git-remote":
        _print(substrate.inspect_remote_git())
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
