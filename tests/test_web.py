import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import cortex.web as web
from cortex.web import Handler


def make_root(tmp_path: Path) -> Path:
    (tmp_path / "runtime").mkdir()
    (tmp_path / "ledger").mkdir()
    (tmp_path / "LAW.md").write_text("# LAW")
    (tmp_path / "runtime" / "permissions.json").write_text(json.dumps({
        "authority_levels": {
            "interpret": {"tools": ["summarize"], "requires_confirmation": False}
        }
    }))
    return tmp_path


def serve(monkeypatch, tmp_path):
    root = make_root(tmp_path)
    monkeypatch.setattr(web, "ROOT", root)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}"


def post(url, payload):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, json.loads(r.read().decode())


def get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, json.loads(r.read().decode())


def test_web_invoke_accepts_and_ledger(monkeypatch, tmp_path):
    server, base = serve(monkeypatch, tmp_path)
    try:
        code, data = post(base + "/invoke", {"task": "summarize", "authority": "interpret", "tools": ["summarize"]})
        assert code == 200
        assert data["status"] == "accepted"
        code, ledger = get(base + "/ledger/actions.jsonl")
        assert code == 200
        actions = [r["action_type"] for r in ledger["records"]]
        assert "invoke" in actions
        assert "oracle_proposal" in actions
    finally:
        server.shutdown()


def test_web_invoke_refuses(monkeypatch, tmp_path):
    server, base = serve(monkeypatch, tmp_path)
    try:
        req = urllib.request.Request(
            base + "/invoke",
            data=json.dumps({"task": "write", "authority": "interpret", "tools": ["write_workspace"]}).encode(),
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
            data = json.loads(exc.read().decode())
            assert data["status"] == "refused"
    finally:
        server.shutdown()


def test_web_oracle(monkeypatch, tmp_path):
    server, base = serve(monkeypatch, tmp_path)
    try:
        code, data = post(base + "/oracle", {"task": "interpret", "authority": "interpret"})
        assert code == 200
        assert data["status"] == "proposed"
        assert data["may_execute"] is False
    finally:
        server.shutdown()


def test_web_self_test(monkeypatch, tmp_path):
    server, base = serve(monkeypatch, tmp_path)
    try:
        code, data = post(base + "/self-test", {})
        assert code == 200
        assert data["status"] == "pass"
    finally:
        server.shutdown()
