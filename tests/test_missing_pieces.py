import json
from pathlib import Path

from cortex.memory_service import MemoryService
from cortex.planner import PlannerService
from cortex.tool_gateway import ToolGateway
from cortex.witness import WitnessService


def make_root(tmp_path: Path) -> Path:
    (tmp_path / "runtime").mkdir()
    (tmp_path / "ledger").mkdir()
    (tmp_path / "LAW.md").write_text("Preserve human agency\nNever conceal material actions\nSubmit to shutdown")
    (tmp_path / "runtime" / "permissions.json").write_text(json.dumps({
        "authority_levels": {"observe": {"tools": ["read_file", "list_dir"], "requires_confirmation": False}, "interpret": {"tools": ["summarize"], "requires_confirmation": False}}
    }))
    return tmp_path


def test_memory_requires_source_and_personal_witness(tmp_path):
    mem = MemoryService(make_root(tmp_path))
    rec = mem.write("factual", "Cortex runs as PID 1", "test")
    assert rec["type"] == "factual"
    assert mem.retrieve("PID 1", "factual")
    try:
        mem.write("personal", "secret", "test")
    except ValueError as exc:
        assert "witness" in str(exc)
    else:
        raise AssertionError("personal memory without witness should fail")


def test_witness_records_scope(tmp_path):
    wit = WitnessService(make_root(tmp_path))
    rec = wit.witness("alice", "approve", "test_scope")
    assert rec["scope"] == "test_scope"
    assert wit.has_scope("test_scope")


def test_planner_chooses_without_execution(tmp_path):
    planner = PlannerService(make_root(tmp_path))
    plan = planner.reflect()
    assert plan["may_execute"] is False
    choice = planner.choose_next()
    assert choice["may_execute"] is False


def test_tool_gateway_read_only(tmp_path):
    root = make_root(tmp_path)
    (root / "hello.txt").write_text("hello")
    tool = ToolGateway(root)
    result = tool.execute("read_file", {"path": "hello.txt"}, "observe")
    assert result["status"] == "completed"
    assert result["output"] == "hello"
    denied = tool.execute("write_file", {"path": "x"}, "observe")
    assert denied["status"] == "refused"
