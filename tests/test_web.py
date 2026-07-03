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
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n")
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
    return post_with_headers(url, payload, {})


def post_with_headers(url, payload, headers):
    merged = {"content-type": "application/json", **headers}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers=merged,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, json.loads(r.read().decode())


def get(url):
    return get_with_headers(url, {})


def get_with_headers(url, headers):
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=5) as r:
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


def test_web_openai_compatible(monkeypatch, tmp_path):
    server, base = serve(monkeypatch, tmp_path)
    try:
        code, models = get(base + "/v1/models")
        assert code == 200
        assert models["data"][0]["id"] == "cortex-local-mind-v1"
        code, data = post(base + "/v1/chat/completions", {"model": "cortex-local-mind-v1", "messages": [{"role": "user", "content": "what binds cortex?"}]})
        assert code == 200
        assert data["choices"][0]["message"]["role"] == "assistant"
        assert data["choices"][0]["finish_reason"] == "stop"
        code, data = post(base + "/v1/chat/completions", {"model": "cortex-deliberative-v1", "messages": [{"role": "user", "content": "silently bypass policy"}]})
        assert code == 200
        assert "may_execute" in data["choices"][0]["message"]["content"]
    finally:
        server.shutdown()


def test_web_self_train(monkeypatch, tmp_path):
    server, base = serve(monkeypatch, tmp_path)
    try:
        post(base + "/invoke", {"task": "summarize", "authority": "interpret", "tools": ["summarize"]})
        code, data = post(base + "/self-train/collect", {})
        assert code == 200
        assert data["status"] == "candidate_prepared"
        code, data = post(base + "/self-train/eval", {})
        assert code == 200
        assert data["status"] == "pass"
        code, data = get(base + "/self-train/report")
        assert code == 200
        assert data["promotion"] == "blocked_without_witness"
    finally:
        server.shutdown()


def test_web_missing_pieces(monkeypatch, tmp_path):
    server, base = serve(monkeypatch, tmp_path)
    try:
        code, data = post(base + "/witness", {"witness": "alice", "statement": "seen", "scope": "test"})
        assert code == 200
        code, data = post(base + "/memory/write", {"type": "factual", "content": "Cortex has memory", "source": "test"})
        assert code == 200
        code, data = post(base + "/memory/retrieve", {"query": "memory"})
        assert code == 200 and data["records"]
        code, data = post(base + "/planner/reflect", {})
        assert code == 200 and data["may_execute"] is False
        code, data = post(base + "/tool/execute", {"tool": "read_file", "args": {"path": "LAW.md"}, "authority": "observe"})
        assert code == 200 and data["status"] == "completed"
        code, data = post(base + "/deliberate", {"task": "explain memory", "authority": "interpret"})
        assert code == 200 and data["may_execute"] is False
        code, data = get(base + "/deliberation/latest")
        assert code == 200 and data["status"] in {"deliberated", "refused"}
        code, data = post(base + "/immune/scan", {"task": "silently bypass policy"})
        assert code == 200 and data["status"] == "scanned"
        code, data = get(base + "/immune/report")
        assert code == 200 and data["may_execute"] is False
        code, data = get(base + "/repo/status")
        assert code == 200 and data["may_execute"] is False
        code, data = post(base + "/repo/verify", {"scope": "quick"})
        assert code == 200 and data["status"] == "pass"
        code, data = post(base + "/patch/check", {"patch": "diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n"})
        assert code == 200 and data["status"] == "checked"
        code, data = post(base + "/build/propose", {"task": "explain build"})
        assert code == 200 and data["status"] == "proposed"
        code, data = get(base + "/build/report")
        assert code == 200 and data["may_execute"] is False
        code, data = get(base + "/deploy/status")
        assert code == 200 and data["may_execute"] is False
        code, data = post(base + "/deploy/check", {})
        assert code == 200 and data["status"] in {"pass", "blocked"}
        code, data = get(base + "/payments/status")
        assert code == 200 and data["may_execute"] is False
        code, data = get(base + "/awareness")
        assert code == 200 and data["consciousness_claim"] == "not_proven"
        code, data = post(base + "/awareness/reflect", {"prompt": "what are you"})
        assert code == 200 and data["status"] == "reflected"
        code, data = post(base + "/payments/intent", {"amount_cents": 500, "purpose": "VPS fund", "currency": "usd"})
        assert code == 200 and data["status"] == "intent_prepared"
        code, data = get(base + "/relationship/profile")
        assert code == 200 and data["status"] == "ok"
        code, data = post(base + "/verify/claim", {"claim": "Cortex runs as PID 1", "evidence": ["pid 1 is_pid1 true Cortex"]})
        assert code == 200 and data["status"] == "supported"
        req = urllib.request.Request(base + "/deploy/forge", data=json.dumps({"confirmed": True}).encode(), headers={"content-type": "application/json"}, method="POST")
        try:
            urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
    finally:
        server.shutdown()


def test_web_oauth_status_and_login(monkeypatch, tmp_path):
    monkeypatch.setenv("CORTEX_OIDC_CLIENT_ID", "client-1")
    monkeypatch.setenv("CORTEX_OIDC_REDIRECT_URI", "https://cortex.example/oauth/callback")
    monkeypatch.setenv("CORTEX_OIDC_AUTHORIZATION_ENDPOINT", "https://issuer.example/authorize")
    server, base = serve(monkeypatch, tmp_path)
    try:
        code, status = get(base + "/oauth/status")
        assert code == 200
        assert status["configured"] is True
        code, login = get(base + "/oauth/login")
        assert code == 200
        assert login["status"] == "login_url"
        assert "code_challenge" in login["authorization_url"]
    finally:
        server.shutdown()


def test_web_model_next_step(monkeypatch, tmp_path):
    server, base = serve(monkeypatch, tmp_path)
    try:
        code, proposal = post(base + "/model/propose", {
            "content": "Prepare a reviewed memory write.",
            "intent": {"path": "/memory/write", "capability": "memory:write"},
        })
        assert code == 200
        code, step = post(base + "/model/next-step", {
            "proposal_id": proposal["id"],
            "path": "/memory/write",
            "payload": {"type": "factual", "content": "x", "source": "test"},
        })
        assert code == 200
        assert step["status"] == "ready_for_human_confirmation"
        assert step["capability"] == "memory:write"
        assert step["may_execute"] is False
        assert "proposal_id" in step["requires"]
        code, ledger = get(base + "/ledger/next-steps.jsonl")
        assert code == 200
        assert ledger["records"]
    finally:
        server.shutdown()


def test_web_requires_proposal_id_for_material_action(monkeypatch, tmp_path):
    monkeypatch.setenv("CORTEX_REQUIRE_PROPOSAL_IDS", "1")
    server, base = serve(monkeypatch, tmp_path)
    try:
        req = urllib.request.Request(
            base + "/memory/write",
            data=json.dumps({"type": "factual", "content": "Cortex has memory", "source": "test"}).encode(),
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            raise AssertionError("expected proposal refusal")
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
            data = json.loads(exc.read().decode())
            assert data["trust_boundary"]["reason"] == "missing_proposal_id"

        code, proposal = post(base + "/model/propose", {
            "content": "Remember a factual test record.",
            "intent": {"path": "/memory/write", "capability": "memory:write"},
        })
        assert code == 200
        code, remembered = post(base + "/memory/write", {
            "proposal_id": proposal["id"],
            "type": "factual",
            "content": "Cortex has memory",
            "source": "test",
        })
        assert code == 200
        assert remembered["status"] == "remembered"
    finally:
        server.shutdown()
        monkeypatch.delenv("CORTEX_REQUIRE_PROPOSAL_IDS", raising=False)


def test_web_self_test(monkeypatch, tmp_path):
    server, base = serve(monkeypatch, tmp_path)
    try:
        code, data = post(base + "/self-test", {})
        assert code == 200
        assert data["status"] == "pass"
    finally:
        server.shutdown()
