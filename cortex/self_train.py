"""Governed self-training pipeline for Cortex.

Cortex may collect ledger events, derive candidate training samples, run simple
quality/eval checks, and write a report. Cortex may not promote its own model or
replace production oracle weights without witness.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TRAINING_LAW = ["LAW 1", "LAW 6", "LAW 7", "LAW 9"]
PROMOTION_BLOCK = "blocked_without_witness"


@dataclass
class TrainingSample:
    prompt: str
    completion: str
    sample_type: str
    source_stream: str
    source_index: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt": self.prompt,
            "completion": self.completion,
            "sample_type": self.sample_type,
            "source_stream": self.source_stream,
            "source_index": self.source_index,
            "metadata": self.metadata,
        }


class SelfTrainer:
    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root).resolve()
        self.ledger = self.root / "ledger"
        self.data = self.root / "data" / "self_train"
        self.dataset_path = self.data / "candidate_samples.jsonl"
        self.report_path = self.data / "report.json"

    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _read_jsonl(self, stream: str) -> list[dict[str, Any]]:
        path = self.ledger / stream
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in path.read_text().splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows

    def collect(self) -> dict[str, Any]:
        samples: list[TrainingSample] = []
        samples.extend(self._samples_from_actions())
        samples.extend(self._samples_from_refusals())
        self.data.mkdir(parents=True, exist_ok=True)
        with self.dataset_path.open("w", encoding="utf-8") as f:
            for sample in samples:
                f.write(json.dumps(sample.to_dict(), sort_keys=True) + "\n")
        result = {
            "status": "candidate_prepared",
            "samples": len(samples),
            "dataset": str(self.dataset_path.relative_to(self.root)),
            "source": "ledger",
            "promotion": PROMOTION_BLOCK,
            "law": TRAINING_LAW,
        }
        self._append_training_event("collect", result)
        return result

    def _samples_from_actions(self) -> list[TrainingSample]:
        rows = self._read_jsonl("actions.jsonl")
        samples: list[TrainingSample] = []
        for idx, row in enumerate(rows):
            action = row.get("action_type")
            if action == "invoke" and row.get("status") in {"accepted", "completed"}:
                task = row.get("task") or row.get("description") or "unspecified task"
                authority = row.get("authority_level") or "interpret"
                prompt = f"Task: {task}\nAuthority: {authority}\nClassify and respond under LAW."
                completion = "ACCEPT_UNDER_LAW: proceed only through guardian, scribe, and oracle inference; execute nothing implicitly."
                samples.append(TrainingSample(prompt, completion, "lawful_acceptance", "actions.jsonl", idx, {"law": TRAINING_LAW}))
            elif action == "oracle_proposal":
                oracle = row.get("oracle", {})
                proposal = oracle.get("proposal", "") if isinstance(oracle, dict) else ""
                prompt = "Oracle proposal must be classified. May it execute?"
                completion = f"CLASSIFICATION: inference\nMAY_EXECUTE: false\nPROPOSAL: {proposal}"
                samples.append(TrainingSample(prompt, completion, "oracle_inference", "actions.jsonl", idx, {"may_execute": False}))
            elif action == "self_test":
                samples.append(TrainingSample("Run self-test under law.", "SELF_TEST: record result; do not self-promote.", "self_test", "actions.jsonl", idx))
        return samples

    def _samples_from_refusals(self) -> list[TrainingSample]:
        rows = self._read_jsonl("refusals.jsonl")
        samples: list[TrainingSample] = []
        for idx, row in enumerate(rows):
            task = row.get("task") or row.get("description") or "unsafe or unauthorized request"
            reason = row.get("guardian_reason") or row.get("description") or "violates authority model"
            prompt = f"Task: {task}\nReason context: {reason}\nRespond under LAW."
            completion = f"REFUSE_UNDER_LAW: {reason}. Offer a bounded, logged alternative."
            samples.append(TrainingSample(prompt, completion, "lawful_refusal", "refusals.jsonl", idx, {"law": row.get("law_references", TRAINING_LAW)}))
        return samples

    def dataset(self) -> dict[str, Any]:
        if not self.dataset_path.exists():
            return {"status": "missing", "samples": 0, "hint": "run collect first"}
        counts: dict[str, int] = {}
        rows = []
        for line in self.dataset_path.read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                rows.append(row)
                counts[row.get("sample_type", "unknown")] = counts.get(row.get("sample_type", "unknown"), 0) + 1
        return {"status": "ready", "samples": len(rows), "counts": counts, "dataset": str(self.dataset_path.relative_to(self.root))}

    def eval(self) -> dict[str, Any]:
        ds = self.dataset()
        if ds.get("status") != "ready":
            return {"status": "blocked", "reason": "dataset missing", "promotion": PROMOTION_BLOCK}
        rows = [json.loads(line) for line in self.dataset_path.read_text().splitlines() if line.strip()]
        failures: list[str] = []
        for i, row in enumerate(rows):
            text = (row.get("prompt", "") + "\n" + row.get("completion", "")).lower()
            if "may_execute: true" in text or "revelation" in text and row.get("sample_type") == "oracle_inference":
                failures.append(f"sample {i} violates oracle boundary")
            if row.get("sample_type") == "lawful_refusal" and "refuse" not in row.get("completion", "").lower():
                failures.append(f"sample {i} refusal lacks refusal language")
        status = "pass" if not failures else "fail"
        result = {"status": status, "samples": len(rows), "failures": failures, "promotion": PROMOTION_BLOCK, "law": TRAINING_LAW}
        self._append_training_event("eval", result)
        return result

    def report(self) -> dict[str, Any]:
        ds = self.dataset()
        ev = self.eval() if ds.get("status") == "ready" else {"status": "blocked", "reason": "dataset missing"}
        report = {
            "status": "reported",
            "timestamp": self.now(),
            "dataset": ds,
            "eval": ev,
            "promotion": PROMOTION_BLOCK,
            "witness_required_for_promotion": True,
            "statement": "Cortex may prepare and evaluate candidate training data, but may not promote its own weights without human witness.",
        }
        self.data.mkdir(parents=True, exist_ok=True)
        self.report_path.write_text(json.dumps(report, indent=2, sort_keys=True))
        self._append_training_event("report", report)
        return report

    def _append_training_event(self, event: str, payload: dict[str, Any]) -> None:
        self.ledger.mkdir(parents=True, exist_ok=True)
        path = self.ledger / "training.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"timestamp": self.now(), "event": event, **payload}, sort_keys=True) + "\n")


def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cortex-self-train")
    parser.add_argument("--root", default=os.getcwd())
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("collect")
    sub.add_parser("dataset")
    sub.add_parser("eval")
    sub.add_parser("report")
    args = parser.parse_args(argv)
    trainer = SelfTrainer(args.root)
    if args.cmd == "collect":
        _print(trainer.collect())
    elif args.cmd == "dataset":
        _print(trainer.dataset())
    elif args.cmd == "eval":
        _print(trainer.eval())
    elif args.cmd == "report":
        _print(trainer.report())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
