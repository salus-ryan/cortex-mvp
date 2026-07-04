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


def test_memory_report_scores_quality_and_duplicates(tmp_path: Path):
    svc = MemoryService(tmp_path)
    svc.write("project", "Cortex should evaluate long horizon memory with explicit quality scores", "test", confidence=0.9)
    svc.write("project", "Cortex should evaluate long horizon memory with explicit quality scores", "test", confidence=0.9)
    svc.write("inferred", "thin", "test", confidence=0.2)

    report = svc.report()

    assert report["status"] == "memory_report"
    assert report["total_active"] == 3
    assert report["avg_quality"] > 0
    assert report["duplicates"][0]["count"] == 2
    assert report["low_quality"]
    assert report["may_execute"] is False


def test_memory_search_returns_ranked_quality_records(tmp_path: Path):
    svc = MemoryService(tmp_path)
    low = svc.write("project", "memory", "test", confidence=0.2)
    high = svc.write("project", "memory search should rank detailed sourced high confidence records", "test", confidence=0.95)

    result = svc.search("memory")

    assert result["status"] == "ok"
    assert result["records"][0]["id"] == high["id"]
    assert result["records"][-1]["id"] == low["id"]
    assert result["records"][0]["quality"]["grade"] == "high"
    assert result["may_execute"] is False
