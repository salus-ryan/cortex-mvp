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
import time
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
    "/relationship/remember": "memory:write",
    "/relationship/converse": "memory:write",
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
        self.runtime = self.root / "runtime"
        self.ledger.mkdir(parents=True, exist_ok=True)
        self.runtime.mkdir(parents=True, exist_ok=True)

    def configured(self) -> bool:
        return bool(os.environ.get("CORTEX_AUTH_TOKEN"))

    def status(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "configured": self.configured(),
            "mode": "token" if self.configured() else "dev_open_no_token_configured",
            "capabilities": sorted(CAPABILITIES),
            "protected_paths": dict(sorted(PATH_CAPABILITIES.items())),
            "signed_intents_required": self.signed_intents_required(),
            "intent_ttl_seconds": self.intent_ttl_seconds(),
            "rate_limit": {
                "max_failures": self.max_failures(),
                "window_seconds": self.window_seconds(),
            },
            "may_execute": False,
        }

    def me(self, headers: dict[str, str]) -> dict[str, Any]:
        decision = self.check(headers, "auth:me", path="/auth/me")
        return {"status": "ok" if decision["allowed"] else "unauthorized", **decision}

    def check(self, headers: dict[str, str], capability: str, path: str = "") -> dict[str, Any]:
        token = os.environ.get("CORTEX_AUTH_TOKEN", "")
        actor = headers.get("x-cortex-actor") or headers.get("X-Cortex-Actor") or "unknown"
        rate_key = self._rate_key(headers, actor, capability, path)
        limited = self._rate_limited(rate_key)
        if limited:
            rec = {"allowed": False, "actor": actor, "capability": capability, "path": path, "reason": "auth_rate_limited", "retry_after_seconds": limited["retry_after_seconds"], "may_execute": False}
            self._record("rate_limited", rec)
            return rec
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
            self._note_failure(rate_key)
            self._record("refused", rec)
            return rec
        allowed_caps = self._allowed_capabilities()
        allowed = "*" in allowed_caps or capability in allowed_caps or capability == "auth:me"
        intent_decision = self._check_signed_intent(headers, token, capability, path) if allowed and capability != "auth:me" else {"allowed": True, "reason": "not_required"}
        allowed = allowed and intent_decision["allowed"]
        rec = {
            "allowed": allowed,
            "actor": actor,
            "capability": capability,
            "path": path,
            "token_hash": hashlib.sha256(token.encode()).hexdigest()[:12],
            "reason": "ok" if allowed else (intent_decision["reason"] if not intent_decision["allowed"] else "capability_not_granted"),
            "intent": {k: v for k, v in intent_decision.items() if k != "signature"},
            "may_execute": False,
        }
        if allowed:
            self._clear_failures(rate_key)
        else:
            self._note_failure(rate_key)
        self._record("allowed" if allowed else "refused", rec)
        return rec

    def protect(self, headers: dict[str, str], path: str) -> dict[str, Any] | None:
        cap = PATH_CAPABILITIES.get(path)
        if not cap:
            return None
        decision = self.check(headers, cap, path)
        if decision["allowed"]:
            return None
        return {"status": "unauthorized", "http_status": 429 if decision.get("reason") == "auth_rate_limited" else 401, **decision}

    def max_failures(self) -> int:
        try:
            return int(os.environ.get("CORTEX_AUTH_MAX_FAILURES", "8"))
        except ValueError:
            return 8

    def window_seconds(self) -> int:
        try:
            return int(os.environ.get("CORTEX_AUTH_WINDOW_SECONDS", "60"))
        except ValueError:
            return 60

    def signed_intents_required(self) -> bool:
        return os.environ.get("CORTEX_REQUIRE_SIGNED_INTENTS", "0").lower() in {"1", "true", "yes"}

    def intent_ttl_seconds(self) -> int:
        try:
            return int(os.environ.get("CORTEX_INTENT_TTL_SECONDS", "300"))
        except ValueError:
            return 300

    def sign_intent(self, token: str, timestamp: str, path: str, capability: str, intent: str) -> str:
        msg = f"{timestamp}.{path}.{capability}.{intent}".encode()
        return hmac.new(token.encode(), msg, hashlib.sha256).hexdigest()

    def _check_signed_intent(self, headers: dict[str, str], token: str, capability: str, path: str) -> dict[str, Any]:
        if not self.signed_intents_required():
            return {"allowed": True, "reason": "not_required"}
        timestamp = headers.get("x-cortex-intent-timestamp") or headers.get("X-Cortex-Intent-Timestamp") or ""
        intent = headers.get("x-cortex-intent") or headers.get("X-Cortex-Intent") or ""
        signature = headers.get("x-cortex-intent-signature") or headers.get("X-Cortex-Intent-Signature") or ""
        if not timestamp or not intent or not signature:
            return {"allowed": False, "reason": "missing_signed_intent"}
        try:
            ts = int(timestamp)
        except ValueError:
            return {"allowed": False, "reason": "invalid_intent_timestamp"}
        age = abs(int(time.time()) - ts)
        if age > self.intent_ttl_seconds():
            return {"allowed": False, "reason": "expired_signed_intent", "age_seconds": age}
        expected = self.sign_intent(token, timestamp, path, capability, intent)
        if not hmac.compare_digest(expected, signature):
            return {"allowed": False, "reason": "invalid_intent_signature", "age_seconds": age}
        return {"allowed": True, "reason": "ok", "age_seconds": age, "intent_hash": hashlib.sha256(intent.encode()).hexdigest()[:12]}

    def _rate_key(self, headers: dict[str, str], actor: str, capability: str, path: str) -> str:
        forwarded = headers.get("x-forwarded-for") or headers.get("X-Forwarded-For") or ""
        ip = forwarded.split(",")[0].strip() or headers.get("x-real-ip") or headers.get("X-Real-IP") or "unknown"
        raw = f"{ip}|{actor}|{capability}|{path}"
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def _failures_path(self) -> Path:
        return self.runtime / "auth_failures.json"

    def _read_failures(self) -> dict[str, list[float]]:
        path = self._failures_path()
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text())
            return {str(k): [float(x) for x in v] for k, v in data.items() if isinstance(v, list)}
        except (json.JSONDecodeError, ValueError):
            return {}

    def _write_failures(self, data: dict[str, list[float]]) -> None:
        self._failures_path().write_text(json.dumps(data, sort_keys=True))

    def _rate_limited(self, key: str) -> dict[str, Any] | None:
        now = time.time()
        window = self.window_seconds()
        failures = [ts for ts in self._read_failures().get(key, []) if now - ts <= window]
        if len(failures) >= self.max_failures():
            retry = max(1, int(window - (now - min(failures))))
            return {"retry_after_seconds": retry, "failures": len(failures)}
        return None

    def _note_failure(self, key: str) -> None:
        now = time.time()
        window = self.window_seconds()
        data = self._read_failures()
        data[key] = [ts for ts in data.get(key, []) if now - ts <= window] + [now]
        self._write_failures(data)

    def _clear_failures(self, key: str) -> None:
        data = self._read_failures()
        if key in data:
            del data[key]
            self._write_failures(data)

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
