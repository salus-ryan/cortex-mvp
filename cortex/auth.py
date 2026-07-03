"""Cortex authentication and capability primitive.

Auth is intentionally small and inspectable. A human supplies CORTEX_AUTH_TOKEN;
Cortex never generates, fetches, or prints secrets. If no token is configured,
read-only endpoints remain open and protected endpoints run in explicit dev-open
mode for local development/backward compatibility.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CAPABILITIES = {
    "memory:write",
    "tool:execute",
    "patch:apply",
    "build:apply",
    "deploy:execute",
    "payments:checkout",
    "immune:quarantine",
    "self_train:execute",
}

PATH_CAPABILITIES = {
    "/memory/write": "memory:write",
    "/tool/execute": "tool:execute",
    "/patch/apply": "patch:apply",
    "/build/apply": "build:apply",
    "/deploy/check": "deploy:execute",
    "/deploy/railway": "deploy:execute",
    "/deploy/forge": "deploy:execute",
    "/payments/checkout": "payments:checkout",
    "/immune/quarantine": "immune:quarantine",
    "/self-train/collect": "self_train:execute",
    "/self-train/eval": "self_train:execute",
}


class AuthService:
    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root).resolve()
        self.ledger = self.root / "ledger"
        self.ledger.mkdir(parents=True, exist_ok=True)

    def configured(self) -> bool:
        return bool(os.environ.get("CORTEX_AUTH_TOKEN"))

    def status(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "configured": self.configured(),
            "mode": "token" if self.configured() else "dev_open_no_token_configured",
            "capabilities": sorted(CAPABILITIES),
            "protected_paths": dict(sorted(PATH_CAPABILITIES.items())),
            "may_execute": False,
        }

    def me(self, headers: dict[str, str]) -> dict[str, Any]:
        decision = self.check(headers, "auth:me", path="/auth/me")
        return {"status": "ok" if decision["allowed"] else "unauthorized", **decision}

    def check(self, headers: dict[str, str], capability: str, path: str = "") -> dict[str, Any]:
        token = os.environ.get("CORTEX_AUTH_TOKEN", "")
        actor = headers.get("x-cortex-actor") or headers.get("X-Cortex-Actor") or "unknown"
        if not token:
            rec = {
                "allowed": True,
                "actor": actor,
                "capability": capability,
                "path": path,
                "mode": "dev_open_no_token_configured",
                "reason": "CORTEX_AUTH_TOKEN not configured",
                "may_execute": False,
            }
            self._record("dev_open", rec)
            return rec
        supplied = self._bearer(headers.get("authorization") or headers.get("Authorization") or "")
        if not supplied or not hmac.compare_digest(supplied, token):
            rec = {"allowed": False, "actor": actor, "capability": capability, "path": path, "reason": "missing_or_invalid_bearer_token", "may_execute": False}
            self._record("refused", rec)
            return rec
        allowed_caps = self._allowed_capabilities()
        allowed = "*" in allowed_caps or capability in allowed_caps or capability == "auth:me"
        rec = {
            "allowed": allowed,
            "actor": actor,
            "capability": capability,
            "path": path,
            "token_hash": hashlib.sha256(token.encode()).hexdigest()[:12],
            "reason": "ok" if allowed else "capability_not_granted",
            "may_execute": False,
        }
        self._record("allowed" if allowed else "refused", rec)
        return rec

    def protect(self, headers: dict[str, str], path: str) -> dict[str, Any] | None:
        cap = PATH_CAPABILITIES.get(path)
        if not cap:
            return None
        decision = self.check(headers, cap, path)
        return None if decision["allowed"] else {"status": "unauthorized", **decision}

    def _allowed_capabilities(self) -> set[str]:
        raw = os.environ.get("CORTEX_AUTH_CAPABILITIES", "*").strip()
        if raw.startswith("["):
            try:
                return {str(x) for x in json.loads(raw)}
            except json.JSONDecodeError:
                return set()
        return {x.strip() for x in raw.split(",") if x.strip()}

    def _bearer(self, value: str) -> str:
        prefix = "Bearer "
        return value[len(prefix):].strip() if value.startswith(prefix) else ""

    def _record(self, event: str, rec: dict[str, Any]) -> None:
        payload = {"timestamp": datetime.now(timezone.utc).isoformat(), "event": event, **rec}
        with (self.ledger / "auth.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, sort_keys=True) + "\n")
