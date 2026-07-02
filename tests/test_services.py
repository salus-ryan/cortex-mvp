import json
from pathlib import Path

from cortex.services import GuardianService, InvocationPipeline, ScribeService


def make_root(tmp_path: Path) -> Path:
    (tmp_path / "runtime").mkdir()
    (tmp_path / "ledger").mkdir()
    (tmp_path / "runtime" / "permissions.json").write_text(json.dumps({
        "authority_levels": {
            "interpret": {"tools": ["summarize", "analyze"], "requires_confirmation": False},
            "act_irreversible": {"tools": [], "requires_confirmation": True}
        }
    }))
    return tmp_path


def test_guardian_accepts_and_refuses(tmp_path):
    root = make_root(tmp_path)
    guardian = GuardianService(root)
    assert guardian.check_invocation("interpret", ["summarize"]).allowed
    denied = guardian.check_invocation("interpret", ["write_workspace"])
    assert not denied.allowed
    assert "outside authority" in denied.reason


def test_scribe_appends_and_reads_tail(tmp_path):
    root = make_root(tmp_path)
    scribe = ScribeService(root)
    rec = scribe.append("actions.jsonl", {"action_type": "test"})
    assert scribe.read_tail("actions.jsonl", 1) == [rec]


def test_invocation_pipeline_logs_acceptance_and_refusal(tmp_path):
    root = make_root(tmp_path)
    pipeline = InvocationPipeline(root)
    accepted = pipeline.invoke({"task": "read law", "authority": "interpret", "tools": ["summarize"], "witness": "tester"})
    assert accepted["status"] == "accepted"

    refused = pipeline.invoke({"task": "mutate", "authority": "interpret", "tools": ["write_workspace"]})
    assert refused["status"] == "refused"
    assert pipeline.scribe.read_tail("refusals.jsonl", 1)[0]["status"] == "refused"


def test_self_test_passes(tmp_path):
    root = make_root(tmp_path)
    result = InvocationPipeline(root).self_test()
    assert result["status"] == "pass"
