import json
from pathlib import Path

from cortex.planner import PlannerService


def test_planner_items_have_objective_metrics_and_priority(tmp_path: Path):
    report = PlannerService(tmp_path).reflect()

    assert report["status"] == "planned"
    assert report["scoring_rule"] == "objective_priority = severity + 10 * measurable_success_metric_count + 5 * evidence_path_count"
    assert report["may_execute"] is False
    for item in report["backlog"]:
        assert isinstance(item["severity"], int)
        assert item["evidence_paths"]
        assert item["success_metrics"]
        assert item["objective_priority"] == item["severity"] + 10 * len(item["success_metrics"]) + 5 * len(item["evidence_paths"])
        assert item["may_execute"] is False


def test_planner_orders_by_objective_priority(tmp_path: Path):
    prophet = tmp_path / "runtime" / "prophet" / "latest.json"
    prophet.parent.mkdir(parents=True)
    prophet.write_text(json.dumps({"status": "fail"}))

    report = PlannerService(tmp_path).reflect()
    priorities = [item["objective_priority"] for item in report["backlog"]]

    assert priorities == sorted(priorities, reverse=True)
    assert report["backlog"][0]["id"] == "repair_prophet_failures"
    assert report["backlog"][0]["success_metrics"][0] == {
        "name": "prophet_status_pass",
        "verifier": "json_equals",
        "target": "runtime/prophet/latest.json:status",
        "expected": "pass",
    }


def test_planner_decomposes_goal_into_objective_phases(tmp_path: Path):
    result = PlannerService(tmp_path).decompose("Build durable recall")

    assert result["status"] == "decomposed"
    assert result["step_count"] == 4
    assert [step["phase"] for step in result["steps"]] == ["observe", "design", "verify", "record"]
    assert all(step["success_metrics"] for step in result["steps"])
    assert all(step["may_execute"] is False for step in result["steps"])
    assert result["may_execute"] is False


def test_planner_counterfactuals_are_numeric_and_sorted(tmp_path: Path):
    result = PlannerService(tmp_path).counterfactuals("Build durable recall")

    assert result["status"] == "counterfactuals"
    assert result["scoring_rule"] == "net_score = expected_benefit - risk"
    assert [option["net_score"] for option in result["options"]] == sorted(
        [option["net_score"] for option in result["options"]], reverse=True
    )
    assert all(option["net_score"] == option["expected_benefit"] - option["risk"] for option in result["options"])
    assert result["may_execute"] is False
