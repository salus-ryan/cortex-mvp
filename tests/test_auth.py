import time
from pathlib import Path

from cortex.auth import AuthService


def test_auth_dev_open_without_token(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CORTEX_AUTH_TOKEN", raising=False)
    svc = AuthService(tmp_path)
    decision = svc.check({}, "deploy:execute", "/deploy/check")
    assert decision["allowed"] is True
    assert decision["mode"] == "dev_open_no_token_configured"
    assert (tmp_path / "ledger" / "auth.jsonl").exists()


def test_auth_requires_bearer_when_configured(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CORTEX_AUTH_TOKEN", "secret")
    svc = AuthService(tmp_path)
    assert svc.check({}, "deploy:execute")["allowed"] is False
    assert svc.check({"authorization": "Bearer wrong"}, "deploy:execute")["allowed"] is False
    assert svc.check({"authorization": "Bearer secret"}, "deploy:execute")["allowed"] is True


def test_auth_capability_restriction(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CORTEX_AUTH_TOKEN", "secret")
    monkeypatch.setenv("CORTEX_AUTH_CAPABILITIES", "memory:write")
    svc = AuthService(tmp_path)
    assert svc.check({"authorization": "Bearer secret"}, "memory:write")["allowed"] is True
    assert svc.check({"authorization": "Bearer secret"}, "deploy:execute")["allowed"] is False


def test_protect_path(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CORTEX_AUTH_TOKEN", "secret")
    svc = AuthService(tmp_path)
    assert svc.protect({"authorization": "Bearer secret"}, "/deploy/check") is None
    refusal = svc.protect({}, "/deploy/check")
    assert refusal and refusal["status"] == "unauthorized"
    assert svc.protect({}, "/health") is None


def test_auth_rate_limits_failures(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CORTEX_AUTH_TOKEN", "secret")
    monkeypatch.setenv("CORTEX_AUTH_MAX_FAILURES", "2")
    monkeypatch.setenv("CORTEX_AUTH_WINDOW_SECONDS", "60")
    svc = AuthService(tmp_path)
    headers = {"authorization": "Bearer wrong", "x-forwarded-for": "203.0.113.9"}
    assert svc.protect(headers, "/deploy/check")["reason"] == "missing_or_invalid_bearer_token"
    assert svc.protect(headers, "/deploy/check")["reason"] == "missing_or_invalid_bearer_token"
    limited = svc.protect(headers, "/deploy/check")
    assert limited["reason"] == "auth_rate_limited"
    assert limited["http_status"] == 429


def test_auth_success_clears_failures(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CORTEX_AUTH_TOKEN", "secret")
    svc = AuthService(tmp_path)
    bad = {"authorization": "Bearer wrong", "x-forwarded-for": "203.0.113.10"}
    good = {"authorization": "Bearer secret", "x-forwarded-for": "203.0.113.10"}
    assert svc.protect(bad, "/deploy/check")
    assert svc.protect(good, "/deploy/check") is None
    assert svc._read_failures() == {}


def test_signed_intents_optional_hardening(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CORTEX_AUTH_TOKEN", "secret")
    monkeypatch.setenv("CORTEX_REQUIRE_SIGNED_INTENTS", "1")
    svc = AuthService(tmp_path)
    assert svc.protect({"authorization": "Bearer secret"}, "/deploy/check")["reason"] == "missing_signed_intent"
    ts = str(int(time.time()))
    intent = '{"path":"/deploy/check","method":"POST"}'
    sig = svc.sign_intent("secret", ts, "/deploy/check", "deploy:execute", intent)
    headers = {
        "authorization": "Bearer secret",
        "x-cortex-intent-timestamp": ts,
        "x-cortex-intent": intent,
        "x-cortex-intent-signature": sig,
    }
    assert svc.protect(headers, "/deploy/check") is None
