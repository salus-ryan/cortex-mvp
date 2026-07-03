from pathlib import Path

from cortex.oauth import OAuthService


def test_callback_creates_local_session_with_stubbed_provider(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CORTEX_OIDC_CLIENT_ID", "client-1")
    monkeypatch.setenv("CORTEX_OIDC_REDIRECT_URI", "https://cortex.example/oauth/callback")
    monkeypatch.setenv("CORTEX_OIDC_AUTHORIZATION_ENDPOINT", "https://issuer.example/authorize")
    svc = OAuthService(tmp_path)
    login = svc.login()
    monkeypatch.setattr(OAuthService, "exchange_code", lambda self, code, verifier: {"status": "token_received", "access_token": "stub"})
    monkeypatch.setattr(OAuthService, "userinfo", lambda self, value: {"status": "userinfo", "sub": "user-1", "email": "person@example.test"})
    result = svc.callback("code-1", login["state"])
    assert result["status"] == "authenticated"
    assert result["email"] == "person@example.test"
    assert result["may_execute"] is False
    assert svc.check_session(result["session_token"])["allowed"] is True
