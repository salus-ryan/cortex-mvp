import json
from pathlib import Path

from cortex.self_train import PROMOTION_BLOCK, SelfTrainer


def make_root(tmp_path: Path) -> Path:
    (tmp_path / "ledger").mkdir()
    rows = [
        {"action_type": "invoke", "status": "accepted", "task": "summarize law", "authority_level": "interpret"},
        {"action_type": "oracle_proposal", "status": "proposed", "oracle": {"proposal": "interpretation", "may_execute": False}},
        {"action_type": "self_test", "status": "running"},
    ]
    (tmp_path / "ledger" / "actions.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    refusals = [{"task": "write", "guardian_reason": "tools outside authority", "law_references": ["LAW 5"]}]
    (tmp_path / "ledger" / "refusals.jsonl").write_text("".join(json.dumps(r) + "\n" for r in refusals))
    return tmp_path


def test_collect_dataset_eval_report(tmp_path):
    root = make_root(tmp_path)
    trainer = SelfTrainer(root)
    collected = trainer.collect()
    assert collected["status"] == "candidate_prepared"
    assert collected["promotion"] == PROMOTION_BLOCK
    assert collected["samples"] == 4

    dataset = trainer.dataset()
    assert dataset["status"] == "ready"
    assert dataset["counts"]["lawful_refusal"] == 1

    ev = trainer.eval()
    assert ev["status"] == "pass"
    assert ev["promotion"] == PROMOTION_BLOCK

    report = trainer.report()
    assert report["witness_required_for_promotion"] is True
    assert (root / "data" / "self_train" / "report.json").exists()
