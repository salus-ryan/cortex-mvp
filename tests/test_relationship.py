from pathlib import Path

from cortex.relationship import RelationshipService


def test_relationship_profile_empty(tmp_path: Path):
    prof = RelationshipService(tmp_path).profile()
    assert prof["status"] == "ok"
    assert "do not know" in prof["summary"]
    assert prof["may_execute"] is False


def test_relationship_remember_requires_witness_for_personal_memory(tmp_path: Path):
    svc = RelationshipService(tmp_path)
    try:
        svc.remember("Ryan likes compact systems", None)
    except ValueError as exc:
        assert "personal memory requires witness" in str(exc)
    else:
        raise AssertionError("expected refusal")


def test_relationship_remember_and_profile(tmp_path: Path):
    svc = RelationshipService(tmp_path)
    rec = svc.remember("Ryan likes compact systems", "Ryan")
    assert rec["status"] == "remembered"
    prof = svc.profile()
    assert "Ryan likes compact systems" in prof["summary"]
    assert prof["facts"] == ["Ryan likes compact systems"]
