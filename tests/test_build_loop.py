import subprocess
from pathlib import Path

from cortex.build_loop import BuildLoopService


def init_repo(root: Path) -> str:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    (root / "hello.txt").write_text("hello\n")
    (root / "LAW.md").write_text("Preserve human agency\nNever conceal material actions\nSubmit to shutdown")
    (root / "tests").mkdir()
    (root / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    (root / "runtime").mkdir()
    children = {name: {"status": "running"} for name in ["web", "guardian", "scribe", "oracle", "prophet", "memory", "tool", "planner", "deliberator", "immune", "repo", "patch", "build"]}
    (root / "runtime" / "pid1.json").write_text(__import__("json").dumps({"is_pid1": True, "children": children}))
    subprocess.run(["git", "add", "hello.txt", "tests/test_ok.py"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True)
    return """diff --git a/hello.txt b/hello.txt
index ce01362..cc628cc 100644
--- a/hello.txt
+++ b/hello.txt
@@ -1 +1 @@
-hello
+hello cortex
"""


def test_build_loop_full_witnessed_cycle(tmp_path: Path):
    patch = init_repo(tmp_path)
    svc = BuildLoopService(tmp_path)
    proposal = svc.propose("change greeting")
    assert proposal["status"] == "proposed"
    checked = svc.check(patch, "change greeting")
    assert checked["status"] == "checked"
    assert checked["may_apply"] is True
    refused = svc.apply(patch, None, True, "change greeting")
    assert refused["status"] == "refused"
    applied = svc.apply(patch, "alice", True, "change greeting")
    assert applied["status"] == "applied"
    verified = svc.verify("quick")
    assert verified["status"] == "verified"
    assert (tmp_path / "ledger" / "build.jsonl").exists()


def test_build_loop_refuses_bad_patch(tmp_path: Path):
    svc = BuildLoopService(tmp_path)
    checked = svc.check("", "empty")
    assert checked["status"] == "refused"
    assert checked["may_apply"] is False
