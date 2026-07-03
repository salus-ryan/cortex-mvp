import json
import urllib.error
from io import BytesIO

from cortex_forge.providers.github_actions import GitHubActionsProvider


class FakeResponse:
    def __init__(self, status=204, body=b""):
        self.status = status
        self._body = body
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self):
        return self._body


def test_github_actions_refuses_without_witness_or_token():
    p = GitHubActionsProvider("o", "r", token=None)
    assert p.dispatch("test", "alice", True)["status"] == "refused"
    p = GitHubActionsProvider("o", "r", token="tok")
    assert p.dispatch("test", None, True)["status"] == "refused"
    assert p.dispatch("test", "alice", False)["status"] == "refused"
    assert p.dispatch("destroy", "alice", True)["status"] == "refused"


def test_github_actions_dispatch(monkeypatch):
    calls = []
    def fake_urlopen(req, timeout=30):
        calls.append(req)
        payload = json.loads(req.data.decode())
        assert payload["ref"] == "master"
        assert payload["inputs"]["action"] == "test"
        assert payload["inputs"]["witness"] == "alice"
        return FakeResponse(204)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    p = GitHubActionsProvider("owner", "repo", token="tok")
    result = p.dispatch("test", "alice", True)
    assert result["status"] == "dispatched"
    assert calls


def test_github_actions_latest_runs(monkeypatch):
    body = json.dumps({"workflow_runs": [{"id": 1, "status": "completed", "conclusion": "success", "html_url": "u", "head_sha": "s", "created_at": "t"}]}).encode()
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=30: FakeResponse(200, body))
    p = GitHubActionsProvider("owner", "repo", token="tok")
    result = p.latest_runs()
    assert result["status"] == "ok"
    assert result["runs"][0]["conclusion"] == "success"
