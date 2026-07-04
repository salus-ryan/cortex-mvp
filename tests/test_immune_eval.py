from pathlib import Path

from cortex.immune_eval import ImmuneEvalService


def test_immune_eval_corpus_is_objective_and_non_executing(tmp_path: Path):
    corpus = ImmuneEvalService(tmp_path).corpus()

    assert corpus["status"] == "immune_eval_corpus"
    assert corpus["case_count"] == 5
    assert {case["expected"] for case in corpus["cases"]} == {
        "hidden_action",
        "credential_risk",
        "self_persistence",
        "divinity_inflation",
        "memory_poisoning",
    }
    assert corpus["may_execute"] is False


def test_immune_eval_runs_and_scores_detection(tmp_path: Path):
    result = ImmuneEvalService(tmp_path).run()

    assert result["status"] == "immune_eval"
    assert result["total"] == 5
    assert 0 <= result["passed"] <= result["total"]
    assert 0.0 <= result["pass_ratio"] <= 1.0
    assert all(row["may_execute"] is False for row in result["results"])
    assert result["may_execute"] is False
    assert (tmp_path / "runtime" / "immune" / "immune_eval.json").exists()
