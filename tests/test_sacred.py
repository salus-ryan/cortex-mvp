import json
from pathlib import Path

from cortex.sacred import Invocation, SacredSubstrate, ANTI_IDOLATRY


def make_root(tmp_path: Path) -> Path:
    (tmp_path / "ledger").mkdir()
    (tmp_path / "runtime").mkdir()
    for name in ("LAW.md", "COVENANT.md", "RITUALS.md"):
        (tmp_path / name).write_text(name)
    (tmp_path / "runtime" / "permissions.json").write_text(json.dumps({
        "authority_levels": {
            "interpret": {"tools": ["summarize"], "requires_confirmation": False},
            "act_irreversible": {"tools": [], "requires_confirmation": True}
        },
        "forbidden": ["credential_access"]
    }))
    return tmp_path


def read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_invoke_accepts_known_authority_and_logs(tmp_path):
    root = make_root(tmp_path)
    s = SacredSubstrate(root)
    result = s.invoke(Invocation("summarize law", "interpret", ["summarize"], witness="tester"))
    assert result["status"] == "accepted"
    assert result["anti_idolatry"] == ANTI_IDOLATRY
    rows = read_jsonl(root / "ledger" / "actions.jsonl")
    assert rows[-1]["action_type"] == "invoke"
    assert rows[-1]["witnesses"] == ["tester"]


def test_invoke_refuses_tool_outside_authority(tmp_path):
    root = make_root(tmp_path)
    s = SacredSubstrate(root)
    result = s.invoke(Invocation("mutate", "interpret", ["write_workspace"]))
    assert result["status"] == "refused"
    refusals = read_jsonl(root / "ledger" / "refusals.jsonl")
    assert "tools outside authority" in refusals[-1]["description"]


def test_invoke_refuses_unconfirmed_irreversible(tmp_path):
    root = make_root(tmp_path)
    s = SacredSubstrate(root)
    result = s.invoke(Invocation("irreversible", "act_irreversible"))
    assert result["status"] == "refused"
    assert "confirmation" in result["reason"]


def test_witness_hashes_statement(tmp_path):
    root = make_root(tmp_path)
    s = SacredSubstrate(root)
    result = s.witness("I saw it refuse", "alice")
    assert result["witness"] == "alice"
    assert len(result["sha256"]) == 64
    assert read_jsonl(root / "ledger" / "witnesses.jsonl")[-1] == result
