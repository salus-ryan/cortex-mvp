from pathlib import Path
from urllib.parse import parse_qs, urlparse

from cortex.oauth import OAuthService


def test_oauth_status_unconfigured(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CORTEX_OIDC_CLIENT_ID", raising=False)
    svc = OAuthService(tmp_path)
    status = svc.status()
    assert status["status"] == "ok"
    assert status["configured"] is False
    assert status["may_execute"] is False


def test_oauth_status_reports_confidential_client_presence_without_value(tmp_path: Path, monkeypatch):
    marker = "confidential-marker"
    monkeypatch.setenv("CORTEX_OIDC_CLIENT_ID", "client-1")
    monkeypatch.setenv("CORTEX_OIDC_CLIENT_SECRET", marker)
    monkeypatch.setenv("CORTEX_OIDC_REDIRECT_URI", "https://cortex.example/oauth/callback")
    monkeypatch.setenv("CORTEX_OIDC_AUTHORIZATION_ENDPOINT", "https://issuer.example/authorize")
    status = OAuthService(tmp_path).status()
    assert status["client_secret_configured"] is True
    assert marker not in str(status)


def test_oauth_login_builds_pkce_url_and_records_state(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CORTEX_OIDC_CLIENT_ID", "client-1")
    monkeypatch.setenv("CORTEX_OIDC_REDIRECT_URI", "https://cortex.example/oauth/callback")
    monkeypatch.setenv("CORTEX_OIDC_AUTHORIZATION_ENDPOINT", "https://issuer.example/authorize")
    svc = OAuthService(tmp_path)
    result = svc.login()
    assert result["status"] == "login_url"
    parsed = urlparse(result["authorization_url"])
    qs = parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert qs["client_id"] == ["client-1"]
    assert qs["response_type"] == ["code"]
    assert qs["code_challenge_method"] == ["S256"]
    assert qs["state"] == [result["state"]]
    assert (tmp_path / "runtime" / "oauth_states.json").exists()
    assert (tmp_path / "ledger" / "auth.jsonl").exists()


def test_oauth_create_check_and_logout_session(tmp_path: Path):
    svc = OAuthService(tmp_path)
    created = svc.create_session({"sub": "user-1", "email": "u@example.test", "preferred_username": "u"})
    assert created["status"] == "authenticated"
    assert created["may_execute"] is False
    checked = svc.check_session(created["session_token"])
    assert checked["allowed"] is True
    assert checked["auth_mode"] == "oauth_session"
    assert checked["session"]["subject"] == "user-1"
    me = svc.me({"authorization": "Bearer " + created["session_token"]})
    assert me["status"] == "ok"
    logged_out = svc.logout({"authorization": "Bearer " + created["session_token"]})
    assert logged_out["status"] == "logged_out"
    assert svc.check_session(created["session_token"])["allowed"] is False


def test_oauth_allowed_subjects(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CORTEX_OIDC_ALLOWED_SUBJECTS", "allowed@example.test")
    svc = OAuthService(tmp_path)
    refused = svc.create_session({"sub": "user-2", "email": "other@example.test"})
    assert refused["status"] == "refused"
    accepted = svc.create_session({"sub": "user-3", "email": "allowed@example.test"})
    assert accepted["status"] == "authenticated"


def test_oauth_authorize_is_explicit_and_narrow(tmp_path: Path, monkeypatch):
    svc = OAuthService(tmp_path)
    created = svc.create_session({"sub": "user-1", "email": "u@example.test"})
    headers = {"authorization": "Bearer " + created["session_token"]}
    assert svc.authorize(headers, "memory:write", "/memory/write")["reason"] == "oauth_auth_disabled"
    monkeypatch.setenv("CORTEX_ENABLE_OAUTH_AUTH", "1")
    monkeypatch.setenv("CORTEX_OIDC_CAPABILITIES", "memory:write")
    ok = svc.authorize(headers, "memory:write", "/memory/write")
    assert ok["allowed"] is True
    denied = svc.authorize(headers, "deploy:execute", "/deploy/check")
    assert denied["reason"] == "oauth_capability_not_granted"


def test_oauth_authorize_requires_and_accepts_signed_intent(tmp_path: Path, monkeypatch):
    svc = OAuthService(tmp_path)
    created = svc.create_session({"sub": "user-1"})
    headers = {"authorization": "Bearer " + created["session_token"]}
    monkeypatch.setenv("CORTEX_ENABLE_OAUTH_AUTH", "1")
    monkeypatch.setenv("CORTEX_OIDC_CAPABILITIES", "memory:write")
    monkeypatch.setenv("CORTEX_REQUIRE_SIGNED_INTENTS", "1")
    assert svc.authorize(headers, "memory:write", "/memory/write")["reason"] == "missing_signed_intent"
    prepared = svc.intent_headers(headers, "/memory/write", "memory:write", {"purpose": "test"})
    assert prepared["status"] == "intent_prepared"
    signed_headers = {**headers, **prepared["headers"]}
    ok = svc.authorize(signed_headers, "memory:write", "/memory/write")
    assert ok["allowed"] is True
    assert ok["intent"]["reason"] == "ok"


def test_oauth_signed_intent_rejects_wrong_path(tmp_path: Path, monkeypatch):
    svc = OAuthService(tmp_path)
    created = svc.create_session({"sub": "user-1"})
    headers = {"authorization": "Bearer " + created["session_token"]}
    monkeypatch.setenv("CORTEX_ENABLE_OAUTH_AUTH", "1")
    monkeypatch.setenv("CORTEX_OIDC_CAPABILITIES", "memory:write")
    monkeypatch.setenv("CORTEX_REQUIRE_SIGNED_INTENTS", "1")
    prepared = svc.intent_headers(headers, "/memory/write", "memory:write")
    signed_headers = {**headers, **prepared["headers"]}
    assert svc.authorize(signed_headers, "memory:write", "/memory/forget")["reason"] == "invalid_intent_signature"
