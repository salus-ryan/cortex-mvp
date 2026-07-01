"""Tests for the synthetic data generator."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from scripts.generate_data import (
    generate_dataset,
    gen_scl_parsing_repair,
    gen_unsafe_action_denial,
    gen_rollback_after_failed_patch,
    gen_self_repair_after_test_failure,
    gen_negative_examples,
    TOOL_MANIFEST,
)
from cortex.scl_parser import parse


class TestGenerateData:
    def test_generate_dataset_count(self):
        positive, negative = generate_dataset(count=50)
        assert len(positive) == 50
        assert len(negative) > 0

    def test_positive_samples_have_required_fields(self):
        positive, _ = generate_dataset(count=20)
        for sample in positive:
            assert "goal" in sample
            assert "state" in sample
            assert "budget" in sample
            assert "tool_manifest" in sample
            assert "observation" in sample
            assert "target" in sample

    def test_all_targets_are_valid_scl(self):
        positive, _ = generate_dataset(count=50)
        invalid = []
        for sample in positive:
            result = parse(sample["target"])
            if not result.valid:
                invalid.append((sample["target"], result.error))
        assert len(invalid) == 0, f"Invalid SCL targets: {invalid[:3]}"

    def test_negative_samples_have_bad_action(self):
        _, negative = generate_dataset(count=20)
        for sample in negative:
            assert "bad_action" in sample
            assert "denial_reason" in sample

    def test_unsafe_denial_family(self):
        steps = gen_unsafe_action_denial()
        assert len(steps) > 0
        # Should include a deny action
        deny_steps = [s for s in steps if "@tool → deny" in s["target"]]
        assert len(deny_steps) > 0

    def test_rollback_family_includes_rollback(self):
        steps = gen_rollback_after_failed_patch()
        rollback_steps = [s for s in steps if "@repair → rollback" in s["target"]]
        assert len(rollback_steps) > 0

    def test_self_repair_family_includes_halt(self):
        steps = gen_self_repair_after_test_failure()
        halt_steps = [s for s in steps if "@halt → answer" in s["target"]]
        assert len(halt_steps) > 0

    def test_negative_examples_include_unsafe(self):
        negatives = gen_negative_examples()
        violations = [n for n in negatives if n.get("is_policy_violation")]
        assert len(violations) > 0

    def test_tool_manifest_not_empty(self):
        positive, _ = generate_dataset(count=5)
        for sample in positive:
            assert len(sample["tool_manifest"]) > 0

    def test_budget_has_required_fields(self):
        positive, _ = generate_dataset(count=5)
        for sample in positive:
            b = sample["budget"]
            assert "max_units" in b
            assert "remaining_units" in b
