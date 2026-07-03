from pathlib import Path

from cortex.memory_service import MemoryService


def test_memory_forget_hides_record(tmp_path: Path):
    svc = MemoryService(tmp_path)
    rec = svc.write("personal", "Ryan likes durable systems", "test", witness="Ryan")
    assert len(svc.retrieve(typ="personal")) == 1
    tomb = svc.forget(rec["id"], "Ryan", "test forget")
    assert tomb["id"] == rec["id"]
    assert svc.retrieve(typ="personal") == []
    exported = svc.export("personal")
    assert rec["id"] in exported["forgotten_ids"]


def test_memory_forget_requires_witness(tmp_path: Path):
    svc = MemoryService(tmp_path)
    rec = svc.write("personal", "Ryan likes memory control", "test", witness="Ryan")
    try:
        svc.forget(rec["id"], None)
    except ValueError as exc:
        assert "requires witness" in str(exc)
    else:
        raise AssertionError("expected refusal")
