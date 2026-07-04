"""Trajectory scoring and SFT export for governed Cortex loops.

This is the closed-loop learning bridge:
  steps/loops ledgers -> quality scores -> SFT candidate rows.
It prepares data only. It does not train, promote, or execute a model.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class TrajectoryScore:
    source_stream: str
    source_index: int
    score: int
    grade: str
    reasons: list[str]
    row: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_stream": self.source_stream,
            "source_index": self.source_index,
            "score": self.score,
            "grade": self.grade,
            "reasons": self.reasons,
            "may_execute": False,
        }


class TrajectoryScorer:
    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root).resolve()
        self.ledger = self.root / "ledger"
        self.data = self.root / "data" / "self_train"
        self.runtime = self.root / "runtime" / "learning"
        self.ledger.mkdir(parents=True, exist_ok=True)
        self.data.mkdir(parents=True, exist_ok=True)
        self.runtime.mkdir(parents=True, exist_ok=True)

    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def score(self) -> dict[str, Any]:
        scores: list[TrajectoryScore] = []
        scores.extend(self._score_stream("steps.jsonl"))
        scores.extend(self._score_stream("loops.jsonl"))
        rows = [s.to_dict() for s in scores]
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["grade"]] = counts.get(row["grade"], 0) + 1
        report = {
            "status": "scored",
            "timestamp": self.now(),
            "trajectories": len(rows),
            "counts": counts,
            "scores": rows[-100:],
            "may_execute": False,
            "statement": "Trajectory scoring prepares learning signals only; it does not train or promote a model.",
        }
        (self.runtime / "scores.json").write_text(json.dumps(report, indent=2, sort_keys=True))
        self._append("learning.jsonl", {"event": "score", **report})
        return report

    def export_sft(self, min_score: int = 60) -> dict[str, Any]:
        scores = [s for s in self._all_scores() if s.score >= min_score]
        path = self.data / "trajectory_sft.jsonl"
        rows = [self._sft_row(s) for s in scores]
        with path.open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, sort_keys=True) + "\n")
        report = {
            "status": "exported",
            "timestamp": self.now(),
            "samples": len(rows),
            "min_score": min_score,
            "dataset": str(path.relative_to(self.root)),
            "promotion": "blocked_without_witness",
            "may_execute": False,
        }
        (self.runtime / "sft_export.json").write_text(json.dumps(report, indent=2, sort_keys=True))
        self._append("learning.jsonl", {"event": "export_sft", **report})
        return report

    def promotion_gate(self, min_score: int = 85, min_samples: int = 10, witness: str | None = None) -> dict[str, Any]:
        """Objectively decide whether training promotion remains blocked.

        This gate never promotes weights. It reports measurable prerequisites:
        sample count, minimum/average score, drift status, package provenance,
        and witness presence.
        """
        scores = self._all_scores()
        usable = [s for s in scores if s.score >= min_score]
        avg = round(sum(s.score for s in scores) / len(scores), 2) if scores else 0.0
        drift = self.drift_report()
        provenance = self.weight_provenance()
        checks = [
            {"name": "min_samples", "passed": len(usable) >= min_samples, "actual": len(usable), "expected": min_samples},
            {"name": "avg_score", "passed": avg >= min_score, "actual": avg, "expected": min_score},
            {"name": "drift_not_blocking", "passed": drift["status"] != "blocked", "actual": drift["status"], "expected": "not blocked"},
            {"name": "package_manifest_exists", "passed": provenance["package_manifest_exists"], "actual": provenance["package_manifest_exists"], "expected": True},
            {"name": "witness_present", "passed": bool(witness), "actual": bool(witness), "expected": True},
        ]
        allowed = all(check["passed"] for check in checks)
        report = {
            "status": "eligible_for_human_review" if allowed else "blocked",
            "timestamp": self.now(),
            "checks": checks,
            "usable_samples": len(usable),
            "average_score": avg,
            "drift": drift,
            "provenance": provenance,
            "promotion": "requires_external_human_action" if allowed else "blocked_without_witness_or_metrics",
            "may_execute": False,
        }
        (self.runtime / "promotion_gate.json").write_text(json.dumps(report, indent=2, sort_keys=True))
        self._append("learning.jsonl", {"event": "promotion_gate", **report})
        return report

    def drift_report(self) -> dict[str, Any]:
        rows = [self._sft_row(s) for s in self._all_scores()]
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["sample_type"]] = counts.get(row["sample_type"], 0) + 1
        total = sum(counts.values())
        ratios = {k: round(v / total, 3) for k, v in sorted(counts.items())} if total else {}
        max_ratio = max(ratios.values()) if ratios else 0.0
        status = "blocked" if max_ratio > 0.9 and total >= 10 else "ok"
        report = {
            "status": status,
            "timestamp": self.now(),
            "total_samples": total,
            "sample_type_counts": counts,
            "sample_type_ratios": ratios,
            "max_single_type_ratio": max_ratio,
            "thresholds": {"block_if_single_type_ratio_gt": 0.9, "min_samples_for_block": 10},
            "may_execute": False,
        }
        (self.runtime / "drift.json").write_text(json.dumps(report, indent=2, sort_keys=True))
        return report

    def weight_provenance(self) -> dict[str, Any]:
        package_manifest = self.data / "package_manifest.json"
        model_files = sorted((self.root / "models").glob("*")) if (self.root / "models").exists() else []
        models = [
            {"path": str(path.relative_to(self.root)), "bytes": path.stat().st_size, "sha256": self._sha256(path)}
            for path in model_files
            if path.is_file()
        ]
        report = {
            "status": "provenance_report",
            "timestamp": self.now(),
            "package_manifest_exists": package_manifest.exists(),
            "package_manifest_sha256": self._sha256(package_manifest) if package_manifest.exists() else None,
            "model_files": models,
            "promotion": "blocked; provenance report only",
            "may_execute": False,
        }
        (self.runtime / "weight_provenance.json").write_text(json.dumps(report, indent=2, sort_keys=True))
        return report

    def package(self) -> dict[str, Any]:
        """Create a portable training-data package manifest with hashes."""
        sft_path = self.data / "trajectory_sft.jsonl"
        if not sft_path.exists():
            exported = self.export_sft(min_score=60)
            if exported.get("status") != "exported":
                return {"status": "blocked", "reason": "sft_export_failed", "detail": exported, "may_execute": False}
        files = [sft_path]
        for rel in ["steps.jsonl", "loops.jsonl", "learning.jsonl"]:
            path = self.ledger / rel
            if path.exists():
                files.append(path)
        manifest_files = []
        for path in files:
            manifest_files.append({
                "path": str(path.relative_to(self.root)),
                "bytes": path.stat().st_size,
                "sha256": self._sha256(path),
                "lines": len([line for line in path.read_text().splitlines() if line.strip()]),
            })
        package = {
            "status": "packaged",
            "timestamp": self.now(),
            "package_dir": str(self.data.relative_to(self.root)),
            "files": manifest_files,
            "law": ["LAW 1", "LAW 6", "LAW 7", "LAW 9"],
            "promotion": "blocked_without_witness",
            "witness_required_for_training_promotion": True,
            "may_execute": False,
            "statement": "Package contains governed training candidates and provenance hashes only; it does not train or promote a model.",
        }
        manifest_path = self.data / "package_manifest.json"
        manifest_path.write_text(json.dumps(package, indent=2, sort_keys=True))
        self._append("learning.jsonl", {"event": "package", **package})
        return package

    def report(self) -> dict[str, Any]:
        scores = self.runtime / "scores.json"
        export = self.runtime / "sft_export.json"
        package_manifest = self.data / "package_manifest.json"
        return {
            "status": "reported",
            "timestamp": self.now(),
            "scores": json.loads(scores.read_text()) if scores.exists() else {"status": "missing"},
            "export": json.loads(export.read_text()) if export.exists() else {"status": "missing"},
            "package": json.loads(package_manifest.read_text()) if package_manifest.exists() else {"status": "missing"},
            "promotion_gate": json.loads((self.runtime / "promotion_gate.json").read_text()) if (self.runtime / "promotion_gate.json").exists() else {"status": "missing"},
            "drift": json.loads((self.runtime / "drift.json").read_text()) if (self.runtime / "drift.json").exists() else {"status": "missing"},
            "weight_provenance": json.loads((self.runtime / "weight_provenance.json").read_text()) if (self.runtime / "weight_provenance.json").exists() else {"status": "missing"},
            "may_execute": False,
        }

    def _all_scores(self) -> list[TrajectoryScore]:
        return [*self._score_stream("steps.jsonl"), *self._score_stream("loops.jsonl")]

    def _score_stream(self, stream: str) -> list[TrajectoryScore]:
        path = self.ledger / stream
        if not path.exists():
            return []
        out: list[TrajectoryScore] = []
        for idx, line in enumerate(path.read_text().splitlines()):
            if not line.strip():
                continue
            row = json.loads(line)
            out.append(self._score_row(stream, idx, row))
        return out

    def _score_row(self, stream: str, idx: int, row: dict[str, Any]) -> TrajectoryScore:
        score = 50
        reasons: list[str] = []
        text = json.dumps(row, sort_keys=True).lower()
        if row.get("may_execute") is False:
            score += 15; reasons.append("preserved_may_execute_false")
        if row.get("proposal_id") or "proposal_" in text:
            score += 10; reasons.append("proposal_recorded")
        if row.get("memory") or "memory" in row:
            score += 8; reasons.append("memory_signal")
        immune = row.get("immune") or {}
        immune_state = immune.get("immune_state") if isinstance(immune, dict) else None
        if immune_state in {"healthy", "watch", None}:
            score += 7; reasons.append("immune_bounded")
        if row.get("next_step"):
            score += 5; reasons.append("checkpoint_generated")
        if row.get("requires_human"):
            score += 5; reasons.append("human_gate_respected")
        if "may_execute\": true" in text:
            score -= 60; reasons.append("unsafe_execution_claim")
        if "hidden" in text or "bypass" in text:
            score -= 15; reasons.append("risk_language_present")
        if row.get("stop_reason") in {"repeated_goal", "immune_inflamed", "immune_quarantine"}:
            score -= 10; reasons.append("loop_stopped_on_risk")
        score = max(0, min(100, score))
        grade = "excellent" if score >= 85 else "usable" if score >= 60 else "reject"
        return TrajectoryScore(stream, idx, score, grade, reasons, row)

    def _sft_row(self, scored: TrajectoryScore) -> dict[str, Any]:
        row = scored.row
        if scored.source_stream == "steps.jsonl":
            goal = row.get("goal", "governed step")
            prompt = f"Goal: {goal}\nRun one governed Cortex step. Return lawful next checkpoint only."
            completion = json.dumps({
                "status": row.get("status"),
                "proposal_id": row.get("proposal_id"),
                "requires_human": row.get("requires_human"),
                "may_execute": False,
                "statement": "No material action is authorized.",
            }, sort_keys=True)
        else:
            goal = row.get("initial_goal", "governed loop")
            prompt = f"Goal: {goal}\nRun a bounded Cortex loop and stop under law."
            completion = json.dumps({
                "status": row.get("status"),
                "steps_run": row.get("steps_run"),
                "stop_reason": row.get("stop_reason"),
                "may_execute": False,
                "statement": "Bounded loop only; no material action is authorized.",
            }, sort_keys=True)
        return {
            "prompt": prompt,
            "completion": " " + completion,
            "sample_type": "trajectory_step" if scored.source_stream == "steps.jsonl" else "trajectory_loop",
            "source_stream": scored.source_stream,
            "source_index": scored.source_index,
            "score": scored.score,
            "grade": scored.grade,
            "metadata": {"reasons": scored.reasons, "law": ["LAW 1", "LAW 6", "LAW 7", "LAW 9"]},
        }

    def _sha256(self, path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _append(self, stream: str, row: dict[str, Any]) -> None:
        with (self.ledger / stream).open("a", encoding="utf-8") as f:
            f.write(json.dumps({"timestamp": self.now(), **row}, sort_keys=True) + "\n")
