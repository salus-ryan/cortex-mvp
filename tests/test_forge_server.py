import json
import subprocess
from pathlib import Path

from cortex_forge.server import ForgeState


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Dockerfile").write_text("FROM scratch\n")
    (repo / "forge").mkdir()
    (repo / "forge" / "deploy.sh").write_text("#!/usr/bin/env bash\necho ok\n")
    (repo / "forge" / "deploy.sh").chmod(0o755)
    (repo / "forge" / "healthcheck.sh").write_text("#!/usr/bin/env bash\necho ok\n")
    (repo / "forge" / "healthcheck.sh").chmod(0o755)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return repo


def test_forge_state_refuses_without_witness(tmp_path):
    state = ForgeState(tmp_path / "state", make_repo(tmp_path))
    assert state.deploy(None, True)["status"] == "refused"
    assert state.deploy("alice", False)["status"] == "refused"


def test_forge_state_check_reports_blockers(tmp_path, monkeypatch):
    state = ForgeState(tmp_path / "state", make_repo(tmp_path))
    monkeypatch.setattr(state, "_cmd_available", lambda cmd: False if cmd == "docker" else True)
    report = state.check()
    assert report["status"] == "blocked"
    assert "docker unavailable" in report["blockers"]


def test_forge_status_contains_git_and_container(tmp_path, monkeypatch):
    state = ForgeState(tmp_path / "state", make_repo(tmp_path))
    monkeypatch.setattr(state, "_cmd_available", lambda cmd: False if cmd == "docker" else True)
    status = state.status()
    assert status["git"]["head"]
    assert status["container"]["available"] is False


def test_forge_update_requires_witness(tmp_path):
    state = ForgeState(tmp_path / "state", make_repo(tmp_path))
    assert state.update_repo(None, True)["status"] == "refused"
    assert state.update_repo("alice", False)["status"] == "refused"


def test_forge_update_branch_mismatch(tmp_path):
    state = ForgeState(tmp_path / "state", make_repo(tmp_path))
    result = state.update_repo("alice", True, expected_branch="not-current")
    assert result["status"] == "refused"
    assert "branch mismatch" in result["reason"]


def test_forge_logs_without_docker(tmp_path, monkeypatch):
    state = ForgeState(tmp_path / "state", make_repo(tmp_path))
    monkeypatch.setattr(state, "_cmd_available", lambda cmd: False)
    assert state.container_logs()["status"] == "unavailable"


def test_forge_state_deploy_allowlisted_script(tmp_path, monkeypatch):
    state = ForgeState(tmp_path / "state", make_repo(tmp_path))
    monkeypatch.setattr(state, "_cmd_available", lambda cmd: True)

    class Proc:
        returncode = 0
        stdout = '{"status":"deployed"}\n'
        stderr = ""

    calls = []
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if str(cmd[0]).endswith("forge/deploy.sh"):
            calls.append(cmd)
            assert kwargs["env"]["WITNESS"] == "alice"
            return Proc()
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)
    report = state.deploy("alice", True)
    assert report["status"] == "deployed"
    assert calls and str(calls[0][0]).endswith("forge/deploy.sh")
    assert json.loads((tmp_path / "state" / "jobs" / "latest.json").read_text())["status"] == "deployed"
