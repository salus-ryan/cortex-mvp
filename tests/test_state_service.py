from pathlib import Path

from cortex.memory_service import MemoryService
from cortex.state_service import StateService


def test_state_export_excludes_auth_and_includes_memory(tmp_path: Path):
    MemoryService(tmp_path).write("personal", "Ryan likes backups", "test", witness="Ryan")
    (tmp_path / "ledger").mkdir(exist_ok=True)
    (tmp_path / "ledger" / "auth.jsonl").write_text("secret\n")
    svc = StateService(tmp_path)
    exported = svc.export()
    paths = [f["path"] for f in exported["bundle"]["files"]]
    assert "memory/personal.jsonl" in paths
    assert "ledger/auth.jsonl" not in paths
    assert exported["may_execute"] is False


def test_state_import_requires_witness_and_confirmation(tmp_path: Path):
    svc = StateService(tmp_path)
    bundle = {"format": "cortex-state-v1", "files": []}
    assert svc.import_bundle(bundle, None, True)["status"] == "refused"
    assert svc.import_bundle(bundle, "Ryan", False)["status"] == "refused"


def test_state_import_restores_memory_only(tmp_path: Path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    MemoryService(src).write("personal", "Ryan likes vaults", "test", witness="Ryan")
    exported = StateService(src).export()["bundle"]
    result = StateService(dst).import_bundle(exported, "Ryan", True)
    assert result["status"] == "imported"
    rows = MemoryService(dst).retrieve(typ="personal")
    assert rows and rows[0]["content"] == "Ryan likes vaults"
