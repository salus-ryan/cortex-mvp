"""Tests for the Trainer module."""

import json
import tempfile
from pathlib import Path
import pytest
from cortex.trainer import format_prompt, format_training_pair, prepare_sft_dataset, load_jsonl


SAMPLE = {
    "goal": "Fix failing pytest",
    "state": {"task_id": "T-001", "phase": "diagnose"},
    "memory": {"repo": "cortex"},
    "budget": {"max_units": 20, "remaining_units": 14},
    "tool_manifest": ["shell.readonly", "pytest"],
    "observation": "pytest shows test_budget_debit fails",
    "target": '@tool → call [name: "pytest", args: "tests/test_budget.py", risk: "verify"]',
}


class TestTrainer:
    def test_format_prompt_contains_goal(self):
        prompt = format_prompt(SAMPLE)
        assert "Fix failing pytest" in prompt

    def test_format_prompt_contains_system(self):
        prompt = format_prompt(SAMPLE)
        assert "SYSTEM:" in prompt
        assert "Cortex policy" in prompt

    def test_format_prompt_contains_sections(self):
        prompt = format_prompt(SAMPLE)
        for section in ["GOAL:", "STATE:", "MEMORY_SUMMARY:", "BUDGET:", "TOOL_MANIFEST:", "LATEST_OBSERVATION:", "NEXT_ACTION:"]:
            assert section in prompt

    def test_format_prompt_ends_with_next_action(self):
        prompt = format_prompt(SAMPLE)
        assert prompt.strip().endswith("NEXT_ACTION:")

    def test_format_training_pair(self):
        pair = format_training_pair(SAMPLE)
        assert "prompt" in pair
        assert "completion" in pair
        assert "pytest" in pair["completion"]

    def test_prepare_sft_dataset(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            pos_path = tmpdir / "positive.jsonl"
            with pos_path.open("w") as f:
                for _ in range(20):
                    f.write(json.dumps(SAMPLE) + "\n")

            train_path, val_path = prepare_sft_dataset(
                positive_path=pos_path,
                output_path=tmpdir / "sft",
                val_split=0.1,
            )
            assert train_path.exists()
            assert val_path.exists()

            train_data = load_jsonl(train_path)
            val_data = load_jsonl(val_path)
            assert len(train_data) + len(val_data) == 20
            assert len(val_data) >= 1

    def test_prepare_sft_dataset_with_negatives(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            pos_path = tmpdir / "positive.jsonl"
            neg_path = tmpdir / "negative.jsonl"

            with pos_path.open("w") as f:
                for _ in range(10):
                    f.write(json.dumps(SAMPLE) + "\n")

            neg_sample = {**SAMPLE, "bad_action": "@tool call [invalid]", "denial_reason": "syntax error"}
            with neg_path.open("w") as f:
                for _ in range(5):
                    f.write(json.dumps(neg_sample) + "\n")

            train_path, val_path = prepare_sft_dataset(
                positive_path=pos_path,
                output_path=tmpdir / "sft",
                negative_path=neg_path,
                val_split=0.1,
            )
            train_data = load_jsonl(train_path)
            val_data = load_jsonl(val_path)
            total = len(train_data) + len(val_data)
            assert total == 15  # 10 positive + 5 negative
