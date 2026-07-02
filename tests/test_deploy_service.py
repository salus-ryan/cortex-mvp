import json
import subprocess
from pathlib import Path

from cortex.deploy_service import DeployService


def make_root(tmp_path: Path) -> Path:
    (tmp_path / "runtime" / "prophet").mkdir(parents=True)
    (tmp_path / "ledger").mkdir()
    (tmp_path / "LAW.md").write_text("Preserve human agency\nNever conceal material actions\nSubmit to shutdown")
    children = {name: {"status": "running"} for name in ["web", "guardian", "scribe", "oracle", "prophet", "memory", "tool", "planner", "deliberator", "immune", "repo", "patch", "build", "deploy"]}
    (tmp_path / "runtime" / "pid1.json").write_text(json.dumps({"is_pid1": True, "children": children}))
    (tmp_path / "runtime" / "permissions.json").write_text(json.dumps({"authority_levels": {"interpret": {"tools": ["summarize"], "requires_confirmation": False}}}))
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("ok\n")
    subprocess.run(["git", "add", "README.md", "LAW.md", "runtime/pid1.json", "runtime/permissions.json"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


def test_deploy_check_blocks_without_railway(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: None)
    report = DeployService(root).check()
    assert report["status"] == "blocked"
    assert "railway CLI unavailable" in report["blockers"]
    assert report["may_execute"] is False


def test_deploy_requires_witness_and_confirmation(tmp_path):
    svc = DeployService(make_root(tmp_path))
    assert svc.railway(None, True)["status"] == "refused"
    assert svc.railway("alice", False)["status"] == "refused"


def test_deploy_forge_requires_witness(tmp_path):
    svc = DeployService(make_root(tmp_path))
    assert svc.forge(None, True)["status"] == "refused"
    assert svc.forge("alice", False)["status"] == "refused"


def test_deploy_forge_allowlisted_script(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    (root / "forge").mkdir()
    (root / "forge" / "deploy.sh").write_text("#!/usr/bin/env bash\necho '{\"status\":\"deployed\"}'\n")
    (root / "forge" / "deploy.sh").chmod(0o755)

    class Proc:
        returncode = 0
        stdout = '{"status":"deployed"}\nforge deploy: pass\n'
        stderr = ""

    calls = []
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if str(cmd[0]).endswith("forge/deploy.sh"):
            calls.append(cmd)
            assert kwargs["env"]["WITNESS"] == "alice"
            assert kwargs["env"]["CONFIRMED"] == "true"
            return Proc()
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)
    report = DeployService(root).forge("alice", True)
    assert report["status"] == "deployed"
    assert calls and str(calls[0][0]).endswith("forge/deploy.sh")


def test_deploy_railway_allowlisted_command(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    monkeypatch.setenv("RAILWAY_TOKEN", "test-token")
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/railway")

    class Proc:
        returncode = 0
        stdout = '{"deploymentId":"dep_123"}'
        stderr = ""

    calls = []
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["railway", "up"]:
            calls.append(cmd)
            return Proc()
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)
    report = DeployService(root).railway("alice", True)
    assert report["status"] == "deployed"
    assert calls == [["railway", "up", "-y", "--detach", "--json"]]
    assert report["deployment"]["deploymentId"] == "dep_123"
