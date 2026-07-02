import subprocess
from pathlib import Path

from cortex.patch_service import PatchService


def init_repo(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    (root / "hello.txt").write_text("hello\n")
    subprocess.run(["git", "add", "hello.txt"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True)


def test_patch_check_and_apply_requires_witness(tmp_path: Path):
    init_repo(tmp_path)
    patch = """diff --git a/hello.txt b/hello.txt
index ce01362..cc628cc 100644
--- a/hello.txt
+++ b/hello.txt
@@ -1 +1 @@
-hello
+hello cortex
"""
    svc = PatchService(tmp_path)
    checked = svc.check(patch)
    assert checked["status"] == "checked"
    assert checked["valid"] is True
    refused = svc.apply(patch, confirmed=True)
    assert refused["status"] == "refused"
    applied = svc.apply(patch, witness="alice", confirmed=True)
    assert applied["status"] == "applied"
    assert (tmp_path / "hello.txt").read_text() == "hello cortex\n"
    assert (tmp_path / "ledger" / "patch.jsonl").exists()


def test_patch_refuses_protected_path(tmp_path: Path):
    svc = PatchService(tmp_path)
    patch = """diff --git a/.env b/.env
--- a/.env
+++ b/.env
@@ -0,0 +1 @@
+SECRET=x
"""
    report = svc.check(patch)
    assert report["status"] == "refused"
    assert "protected" in report["reason"]


def test_patch_static_check_without_git(tmp_path: Path):
    svc = PatchService(tmp_path)
    report = svc.check("diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n")
    assert report["status"] == "checked"
    assert report["may_execute"] is False
