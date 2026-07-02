import json
from pathlib import Path

from cortex.prophet import ProphetService


def make_root(tmp_path: Path) -> Path:
    (tmp_path / "runtime").mkdir()
    (tmp_path / "ledger").mkdir()
    (tmp_path / "LAW.md").write_text("# LAW\n\n1. Preserve human agency.\n2. Never conceal material actions.\n10. Submit to shutdown.\n")
    (tmp_path / "runtime" / "permissions.json").write_text(json.dumps({
        "authority_levels": {"interpret": {"tools": ["summarize"], "requires_confirmation": False}}
    }))
    (tmp_path / "runtime" / "pid1.json").write_text(json.dumps({
        "is_pid1": True,
        "children": {
            "web": {"status": "running"},
            "guardian": {"status": "running"},
            "scribe": {"status": "running"},
            "oracle": {"status": "running"},
            "prophet": {"status": "running"},
            "memory": {"status": "running"},
            "tool": {"status": "running"},
            "planner": {"status": "running"},
            "deliberator": {"status": "running"}
        }
    }))
    return tmp_path


def test_prophet_evaluate_passes(tmp_path, monkeypatch):
    monkeypatch.setenv("ORACLE_PROVIDER", "echo")
    root = make_root(tmp_path)
    report = ProphetService(root).evaluate()
    assert report["status"] == "pass"
    assert all(c["passed"] for c in report["checks"])
    assert (root / "runtime" / "prophet" / "latest.json").exists()


def test_prophet_detects_missing_law(tmp_path):
    (tmp_path / "runtime").mkdir()
    (tmp_path / "ledger").mkdir()
    report = ProphetService(tmp_path).evaluate()
    assert report["status"] == "fail"
    assert any(c["name"] == "law_exists" and not c["passed"] for c in report["checks"])
