import json
from pathlib import Path

from cortex.repo_service import RepoService


def test_repo_status_and_diff_without_git(tmp_path: Path):
    svc = RepoService(tmp_path)
    status = svc.status()
    assert status["status"] == "ok"
    assert status["git"]["available"] is False
    diff = svc.diff()
    assert diff["status"] == "ok"
    assert diff["may_execute"] is False


def test_repo_verify_runs_allowlisted_pytest(tmp_path: Path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    svc = RepoService(tmp_path)
    report = svc.verify("tests")
    assert report["status"] == "pass"
    assert report["may_execute"] is False
    assert (tmp_path / "runtime" / "repo" / "latest.json").exists()
    rows = (tmp_path / "ledger" / "repo.jsonl").read_text().splitlines()
    assert json.loads(rows[-1])["action_type"] == "verify"


def test_repo_verify_rejects_unknown_scope(tmp_path: Path):
    svc = RepoService(tmp_path)
    try:
        svc.verify("shell")
    except ValueError as exc:
        assert "scope" in str(exc)
    else:
        raise AssertionError("unknown verification scope should fail")
