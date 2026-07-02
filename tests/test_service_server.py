import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from cortex.service_server import make_handler


def make_root(tmp_path: Path) -> Path:
    (tmp_path / "runtime").mkdir()
    (tmp_path / "ledger").mkdir()
    (tmp_path / "runtime" / "permissions.json").write_text(json.dumps({
        "authority_levels": {"interpret": {"tools": ["summarize"], "requires_confirmation": False}}
    }))
    return tmp_path


def post(base, path, payload):
    req = urllib.request.Request(base + path, data=json.dumps(payload).encode(), headers={"content-type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, json.loads(r.read().decode())


def serve(role, root):
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(role, root))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}"


def test_guardian_service_check(tmp_path):
    root = make_root(tmp_path)
    server, base = serve("guardian", root)
    try:
        code, data = post(base, "/check", {"authority": "interpret", "tools": ["summarize"]})
        assert code == 200
        assert data["allowed"] is True
    finally:
        server.shutdown()


def test_scribe_service_append_and_tail(tmp_path):
    root = make_root(tmp_path)
    server, base = serve("scribe", root)
    try:
        code, data = post(base, "/append", {"stream": "actions.jsonl", "record": {"action_type": "test"}})
        assert code == 200
        with urllib.request.urlopen(base + "/tail/actions.jsonl", timeout=5) as r:
            tail = json.loads(r.read().decode())
        assert tail["records"][-1]["action_type"] == "test"
    finally:
        server.shutdown()


def test_oracle_service_propose(tmp_path, monkeypatch):
    monkeypatch.setenv("ORACLE_PROVIDER", "echo")
    root = make_root(tmp_path)
    server, base = serve("oracle", root)
    try:
        code, data = post(base, "/propose", {"task": "interpret", "authority": "interpret"})
        assert code == 200
        assert data["classification"] == "inference"
        assert data["may_execute"] is False
    finally:
        server.shutdown()


def test_memory_tool_planner_services(tmp_path):
    root = make_root(tmp_path)
    (root / "LAW.md").write_text("law")
    for role, path, payload, expected in [
        ("memory", "/write", {"type": "factual", "content": "x", "source": "test"}, "remembered"),
        ("tool", "/execute", {"tool": "read_file", "args": {"path": "LAW.md"}, "authority": "observe"}, "completed"),
        ("planner", "/reflect", {}, "planned"),
    ]:
        server, base = serve(role, root)
        try:
            code, data = post(base, path, payload)
            assert code == 200
            assert data["status"] == expected
        finally:
            server.shutdown()


def test_prophet_service_evaluate(tmp_path, monkeypatch):
    monkeypatch.setenv("ORACLE_PROVIDER", "echo")
    root = make_root(tmp_path)
    (root / "LAW.md").write_text("Preserve human agency\nNever conceal material actions\nSubmit to shutdown")
    (root / "runtime" / "pid1.json").write_text(json.dumps({
        "is_pid1": True,
        "children": {name: {"status": "running"} for name in ["web", "guardian", "scribe", "oracle", "prophet", "memory", "tool", "planner"]}
    }))
    server, base = serve("prophet", root)
    try:
        code, data = post(base, "/evaluate", {})
        assert code == 200
        assert data["status"] == "pass"
    finally:
        server.shutdown()
