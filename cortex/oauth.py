"""Generic OIDC login helpers for Cortex.

This is a PKCE-first OAuth/OIDC membrane: it identifies a human, records auth
state, and never grants execution authority by itself.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class OAuthService:
    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root).resolve()
        self.runtime = self.root / "runtime"
        self.ledger = self.root / "ledger"
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.ledger.mkdir(parents=True, exist_ok=True)

    def configured(self) -> bool:
        return bool(self.client_id() and self.redirect_uri() and (self.issuer() or self.authorization_endpoint()))

    def status(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "configured": self.configured(),
            "provider": "generic_oidc_pkce",
            "issuer": self.issuer(),
            "client_id_configured": bool(self.client_id()),
            "client_secret_configured": bool(self.client_secret()),
            "redirect_uri": self.redirect_uri(),
            "scopes": self.scopes(),
            "allowed_subjects_configured": bool(self.allowed_subjects()),
            "session_ttl_seconds": self.session_ttl_seconds(),
            "may_execute": False,
        }

    def login(self) -> dict[str, Any]:
        if not self.configured():
            return {"status": "refused", "reason": "oidc_not_configured", **self.status()}
        endpoints = self.endpoints()
        if not endpoints.get("authorization_endpoint"):
            return {"status": "refused", "reason": "authorization_endpoint_missing", "may_execute": False}
        state = "state_" + uuid.uuid4().hex
        nonce = "nonce_" + uuid.uuid4().hex
        verifier = self._new_verifier()
        challenge = self._challenge(verifier)
        states = self._read_json(self._states_path(), {})
        states[state] = {"created_at": time.time(), "nonce": nonce, "verifier": verifier}
        self._write_json(self._states_path(), states)
        params = {
            "response_type": "code",
            "client_id": self.client_id(),
            "redirect_uri": self.redirect_uri(),
            "scope": self.scopes(),
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        url = endpoints["authorization_endpoint"] + "?" + urllib.parse.urlencode(params)
        self._record("login_started", {"state_hash": self._hash(state), "issuer": self.issuer()})
        return {"status": "login_url", "authorization_url": url, "state": state, "may_execute": False}

    def callback(self, code: str, state: str) -> dict[str, Any]:
        if not code or not state:
            return {"status": "refused", "reason": "code_and_state_required", "may_execute": False}
        states = self._read_json(self._states_path(), {})
        saved = states.pop(state, None)
        self._write_json(self._states_path(), states)
        if not saved:
            self._record("callback_refused", {"reason": "invalid_state"})
            return {"status": "refused", "reason": "invalid_state", "may_execute": False}
        if time.time() - float(saved.get("created_at", 0)) > 600:
            self._record("callback_refused", {"reason": "expired_state"})
            return {"status": "refused", "reason": "expired_state", "may_execute": False}
        token_result = self.exchange_code(code, str(saved.get("verifier", "")))
        if token_result.get("status") != "token_received":
            return token_result
        user = self.userinfo(str(token_result.get("access_token", "")))
        if user.get("status") != "userinfo":
            return user
        return self.create_session(user)

    def exchange_code(self, code: str, verifier: str) -> dict[str, Any]:
        endpoint = self.endpoints().get("token_endpoint")
        if not endpoint:
            return {"status": "refused", "reason": "token_endpoint_missing", "may_execute": False}
        fields = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri(),
            "client_id": self.client_id(),
            "code_verifier": verifier,
        }
        if self.client_secret():
            fields["client_secret"] = self.client_secret()
        body = urllib.parse.urlencode(fields).encode()
        req = urllib.request.Request(endpoint, data=body, headers={"content-type": "application/x-www-form-urlencoded"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if not data.get("access_token"):
                return {"status": "refused", "reason": "access_token_missing", "may_execute": False}
            return {"status": "token_received", **data}
        except Exception as exc:
            self._record("token_exchange_refused", {"reason": type(exc).__name__})
            return {"status": "refused", "reason": "token_exchange_failed", "may_execute": False}

    def userinfo(self, bearer_value: str) -> dict[str, Any]:
        endpoint = self.endpoints().get("userinfo_endpoint")
        if not endpoint:
            return {"status": "refused", "reason": "userinfo_endpoint_missing", "may_execute": False}
        req = urllib.request.Request(endpoint, headers={"authorization": "Bearer " + bearer_value}, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return {"status": "userinfo", **data}
        except Exception as exc:
            self._record("userinfo_refused", {"reason": type(exc).__name__})
            return {"status": "refused", "reason": "userinfo_failed", "may_execute": False}

    def create_session(self, user: dict[str, Any]) -> dict[str, Any]:
        subject = str(user.get("sub", ""))
        if not subject:
            return {"status": "refused", "reason": "userinfo_missing_sub", "may_execute": False}
        if not self._subject_allowed(user):
            self._record("callback_refused", {"reason": "subject_not_allowed", "subject_hash": self._hash(subject)})
            return {"status": "refused", "reason": "subject_not_allowed", "may_execute": False}
        session_value = "oauth_" + uuid.uuid4().hex + uuid.uuid4().hex
        expires_at = time.time() + self.session_ttl_seconds()
        session = {
            "session_hash": self._hash(session_value),
            "subject": subject,
            "email": user.get("email"),
            "preferred_username": user.get("preferred_username"),
            "name": user.get("name"),
            "issuer": self.issuer(),
            "created_at": time.time(),
            "expires_at": expires_at,
        }
        sessions = self._read_json(self._sessions_path(), {})
        sessions[session["session_hash"]] = session
        self._write_json(self._sessions_path(), sessions)
        self._record("session_created", {"session_hash": session["session_hash"], "subject_hash": self._hash(subject)})
        return {
            "status": "authenticated",
            "session_token": session_value,
            "expires_at": datetime.fromtimestamp(expires_at, timezone.utc).isoformat(),
            "subject": subject,
            "email": user.get("email"),
            "preferred_username": user.get("preferred_username"),
            "may_execute": False,
        }

    def check_session(self, session_value: str) -> dict[str, Any]:
        if not session_value:
            return {"allowed": False, "reason": "missing_oauth_session", "may_execute": False}
        session_hash = self._hash(session_value)
        sessions = self._read_json(self._sessions_path(), {})
        session = sessions.get(session_hash)
        if not session:
            return {"allowed": False, "reason": "oauth_session_not_found", "may_execute": False}
        if time.time() > float(session.get("expires_at", 0)):
            del sessions[session_hash]
            self._write_json(self._sessions_path(), sessions)
            return {"allowed": False, "reason": "oauth_session_expired", "may_execute": False}
        return {"allowed": True, "reason": "ok", "auth_mode": "oauth_session", "session_hash": session_hash, "session": {k: v for k, v in session.items() if k != "session_hash"}, "may_execute": False}

    def me(self, headers: dict[str, str]) -> dict[str, Any]:
        decision = self.check_session(self._bearer(headers.get("authorization") or headers.get("Authorization") or ""))
        return {"status": "ok" if decision["allowed"] else "unauthorized", **decision}

    def logout(self, headers: dict[str, str]) -> dict[str, Any]:
        session_value = self._bearer(headers.get("authorization") or headers.get("Authorization") or "")
        session_hash = self._hash(session_value) if session_value else ""
        sessions = self._read_json(self._sessions_path(), {})
        existed = bool(session_hash and session_hash in sessions)
        if existed:
            del sessions[session_hash]
            self._write_json(self._sessions_path(), sessions)
        self._record("session_logout", {"session_hash": session_hash, "existed": existed})
        return {"status": "logged_out", "existed": existed, "may_execute": False}

    def authorize(self, headers: dict[str, str], capability: str, path: str = "") -> dict[str, Any]:
        if os.environ.get("CORTEX_ENABLE_OAUTH_AUTH", "0").lower() not in {"1", "true", "yes"}:
            return {"allowed": False, "reason": "oauth_auth_disabled", "may_execute": False}
        session_value = self._bearer(headers.get("authorization") or headers.get("Authorization") or "")
        decision = self.check_session(session_value)
        if not decision["allowed"]:
            return decision
        caps = self.allowed_capabilities()
        if "*" not in caps and capability not in caps and capability != "auth:me":
            return {"allowed": False, "reason": "oauth_capability_not_granted", "capability": capability, "path": path, "may_execute": False}
        intent_decision = self._check_signed_intent(headers, session_value, capability, path)
        if not intent_decision["allowed"]:
            return {**intent_decision, "capability": capability, "path": path, "may_execute": False}
        self._record("session_authorized", {"session_hash": decision.get("session_hash"), "capability": capability, "path": path, "intent": intent_decision})
        return {"allowed": True, "reason": "ok", "capability": capability, "path": path, "session_hash": decision.get("session_hash"), "intent": intent_decision, "may_execute": False}

    def intent_headers(self, headers: dict[str, str], path: str, capability: str, intent: dict[str, Any] | None = None) -> dict[str, Any]:
        session_value = self._bearer(headers.get("authorization") or headers.get("Authorization") or "")
        decision = self.check_session(session_value)
        if not decision["allowed"]:
            return {"status": "refused", **decision}
        caps = self.allowed_capabilities()
        if "*" not in caps and capability not in caps and capability != "auth:me":
            return {"status": "refused", "reason": "oauth_capability_not_granted", "capability": capability, "path": path, "may_execute": False}
        timestamp = str(int(time.time()))
        payload = {"method": "POST", "path": path, "capability": capability, **dict(intent or {})}
        intent_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        signature = self.sign_intent(session_value, timestamp, path, capability, intent_json)
        result = {
            "status": "intent_prepared",
            "path": path,
            "capability": capability,
            "headers": {
                "x-cortex-intent-timestamp": timestamp,
                "x-cortex-intent": intent_json,
                "x-cortex-intent-signature": signature,
            },
            "may_execute": False,
        }
        self._record("intent_prepared", {"session_hash": decision.get("session_hash"), "path": path, "capability": capability, "intent_hash": self._hash(intent_json)})
        return result

    def sign_intent(self, session_value: str, timestamp: str, path: str, capability: str, intent: str) -> str:
        import hmac
        msg = f"{timestamp}.{path}.{capability}.{intent}".encode()
        return hmac.new(session_value.encode(), msg, hashlib.sha256).hexdigest()

    def endpoints(self) -> dict[str, str]:
        endpoints = {
            "authorization_endpoint": self.authorization_endpoint(),
            "token_endpoint": os.environ.get("CORTEX_OIDC_TOKEN_ENDPOINT", ""),
            "userinfo_endpoint": os.environ.get("CORTEX_OIDC_USERINFO_ENDPOINT", ""),
        }
        if all(endpoints.values()) or not self.issuer():
            return endpoints
        discovery_url = self.issuer().rstrip("/") + "/.well-known/openid-configuration"
        try:
            with urllib.request.urlopen(discovery_url, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            for key in endpoints:
                endpoints[key] = endpoints[key] or str(data.get(key, ""))
            self._write_json(self.runtime / "oidc_discovery.json", data)
        except Exception:
            cached = self._read_json(self.runtime / "oidc_discovery.json", {})
            for key in endpoints:
                endpoints[key] = endpoints[key] or str(cached.get(key, ""))
        return endpoints

    def issuer(self) -> str:
        return os.environ.get("CORTEX_OIDC_ISSUER", "").rstrip("/")

    def authorization_endpoint(self) -> str:
        return os.environ.get("CORTEX_OIDC_AUTHORIZATION_ENDPOINT", "")

    def client_id(self) -> str:
        return os.environ.get("CORTEX_OIDC_CLIENT_ID", "")

    def redirect_uri(self) -> str:
        return os.environ.get("CORTEX_OIDC_REDIRECT_URI", "")

    def client_secret(self) -> str:
        return os.environ.get("CORTEX_OIDC_CLIENT_SECRET", "")

    def scopes(self) -> str:
        return os.environ.get("CORTEX_OIDC_SCOPES", "openid profile email")

    def allowed_subjects(self) -> set[str]:
        raw = os.environ.get("CORTEX_OIDC_ALLOWED_SUBJECTS", "").strip()
        return {x.strip() for x in raw.split(",") if x.strip()}

    def session_ttl_seconds(self) -> int:
        try:
            return int(os.environ.get("CORTEX_OIDC_SESSION_TTL_SECONDS", "28800"))
        except ValueError:
            return 28800

    def allowed_capabilities(self) -> set[str]:
        raw = os.environ.get("CORTEX_OIDC_CAPABILITIES", "auth:me").strip()
        return {x.strip() for x in raw.split(",") if x.strip()}

    def signed_intents_required(self) -> bool:
        return os.environ.get("CORTEX_REQUIRE_SIGNED_INTENTS", "0").lower() in {"1", "true", "yes"}

    def intent_ttl_seconds(self) -> int:
        try:
            return int(os.environ.get("CORTEX_INTENT_TTL_SECONDS", "300"))
        except ValueError:
            return 300

    def _check_signed_intent(self, headers: dict[str, str], session_value: str, capability: str, path: str) -> dict[str, Any]:
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
        import hmac
        expected = self.sign_intent(session_value, timestamp, path, capability, intent)
        if not hmac.compare_digest(expected, signature):
            return {"allowed": False, "reason": "invalid_intent_signature", "age_seconds": age}
        semantic = self._validate_intent_payload(intent, path, capability)
        if not semantic["allowed"]:
            return {**semantic, "age_seconds": age}
        return {"allowed": True, "reason": "ok", "age_seconds": age, "intent_hash": self._hash(intent), **semantic}

    def _validate_intent_payload(self, intent: str, path: str, capability: str) -> dict[str, Any]:
        try:
            data = json.loads(intent)
        except json.JSONDecodeError:
            return {"allowed": True, "reason": "opaque_intent"}
        if not isinstance(data, dict):
            return {"allowed": False, "reason": "invalid_intent_payload"}
        if data.get("path") is not None and str(data.get("path")) != path:
            return {"allowed": False, "reason": "intent_path_mismatch"}
        if data.get("capability") is not None and str(data.get("capability")) != capability:
            return {"allowed": False, "reason": "intent_capability_mismatch"}
        if data.get("method") is not None and str(data.get("method")).upper() != "POST":
            return {"allowed": False, "reason": "intent_method_mismatch"}
        return {"allowed": True, "reason": "ok", "intent_fields": sorted(str(k) for k in data.keys())}

    def _subject_allowed(self, user: dict[str, Any]) -> bool:
        allowed = self.allowed_subjects()
        if not allowed:
            return True
        candidates = {str(user.get("sub", "")), str(user.get("email", "")), str(user.get("preferred_username", ""))}
        return bool(allowed & candidates)

    def _states_path(self) -> Path:
        return self.runtime / "oauth_states.json"

    def _sessions_path(self) -> Path:
        return self.runtime / "oauth_sessions.json"

    def _read_json(self, path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return default

    def _write_json(self, path: Path, data: Any) -> None:
        path.write_text(json.dumps(data, indent=2, sort_keys=True))

    def _record(self, event: str, rec: dict[str, Any]) -> None:
        payload = {"timestamp": datetime.now(timezone.utc).isoformat(), "event": "oauth_" + event, **rec}
        with (self.ledger / "auth.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, sort_keys=True) + "\n")

    def _hash(self, value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()[:16]

    def _bearer(self, value: str) -> str:
        prefix = "Bearer "
        return value[len(prefix):].strip() if value.startswith(prefix) else ""

    def _new_verifier(self) -> str:
        raw = uuid.uuid4().hex + uuid.uuid4().hex + uuid.uuid4().hex
        return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")[:96]

    def _challenge(self, verifier: str) -> str:
        digest = hashlib.sha256(verifier.encode()).digest()
        return base64.urlsafe_b64encode(digest).decode().rstrip("=")
