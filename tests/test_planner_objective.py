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
