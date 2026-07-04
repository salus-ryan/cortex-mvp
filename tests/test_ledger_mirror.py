import json
from pathlib import Path

from cortex.ledger_mirror import LedgerMirrorService


def test_ledger_mirror_manifest_is_hash_chained(tmp_path: Path):
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    (ledger / "a.jsonl").write_text(json.dumps({"a": 1}) + "\n")
    (ledger / "b.jsonl").write_text(json.dumps({"b": 2}) + "\n")

    manifest = LedgerMirrorService(tmp_path).manifest()

    assert manifest["status"] == "ledger_mirror_manifest"
    assert manifest["stream_count"] == 2
    assert len(manifest["root_chain_hash"]) == 64
    assert all(len(stream["sha256"]) == 64 for stream in manifest["streams"])
    assert all(len(stream["chain_hash"]) == 64 for stream in manifest["streams"])
    assert manifest["external_copy_required"] is True
    assert manifest["may_execute"] is False


def test_ledger_mirror_verify_detects_current_manifest(tmp_path: Path):
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    (ledger / "a.jsonl").write_text(json.dumps({"a": 1}) + "\n")
    svc = LedgerMirrorService(tmp_path)
    manifest = svc.manifest()

    verified = svc.verify_manifest(manifest)

    assert verified["status"] == "ledger_mirror_verify"
    assert verified["verified"] is True
    assert all(check["passed"] for check in verified["checks"])
    assert verified["may_execute"] is False
