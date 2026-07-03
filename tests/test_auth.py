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
