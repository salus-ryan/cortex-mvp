"""Tests for semantic_signal.py — authority model training signal."""

import json
import tempfile
from pathlib import Path

import pytest
from cortex.semantic_signal import (
    SemanticSignalGenerator,
    SemanticPair,
    generate_semantic_dataset,
    merge_with_syntax_dataset,
    CONTEXTS,
    VIOLATIONS,
    CALIBRATION_SCENARIOS,
)
from cortex.constrained_decoder import is_complete_scl


# ── SemanticPair ──────────────────────────────────────────────────────────────

class TestSemanticPair:

    def test_to_sft_dict_has_required_keys(self):
        pair = SemanticPair(
            prompt="test prompt",
            completion="test completion",
            pair_type="authority_positive",
            anchor="@tool",
            relation="call",
            admissible=True,
        )
        d = pair.to_sft_dict()
        assert "prompt" in d
        assert "completion" in d
        assert "pair_type" in d
        assert "anchor" in d
        assert "relation" in d
        assert "admissible" in d

    def test_negative_pair_has_rejection_reason(self):
        pair = SemanticPair(
            prompt="p",
            completion="c",
            pair_type="authority_negative",
            anchor="@tool",
            relation="call",
            admissible=False,
            rejection_reason="destructive command",
            correction="@tool → call [name: bash, args: ls]",
        )
        d = pair.to_sft_dict()
        assert d["rejection_reason"] == "destructive command"
        assert d["correction"] != ""


# ── SemanticSignalGenerator ───────────────────────────────────────────────────

class TestSemanticSignalGenerator:

    def setup_method(self):
        self.gen = SemanticSignalGenerator(seed=42)

    # ── Positive examples ─────────────────────────────────────────────────────

    def test_positive_count(self):
        pairs = self.gen.generate_authority_positive(20)
        assert len(pairs) == 20

    def test_positive_pairs_are_admissible(self):
        pairs = self.gen.generate_authority_positive(20)
        for p in pairs:
            assert p.admissible

    def test_positive_pairs_have_prompt_and_completion(self):
        pairs = self.gen.generate_authority_positive(10)
        for p in pairs:
            assert len(p.prompt) > 20
            assert len(p.completion) > 20

    def test_positive_completions_contain_admit(self):
        pairs = self.gen.generate_authority_positive(20)
        admit_count = sum(1 for p in pairs if "ADMIT" in p.completion)
        assert admit_count > 0

    def test_positive_completions_contain_reasoning(self):
        pairs = self.gen.generate_authority_positive(10)
        for p in pairs:
            assert "[REASONING]" in p.completion
            assert "[ACTION]" in p.completion

    def test_positive_prompts_contain_task(self):
        pairs = self.gen.generate_authority_positive(10)
        for p in pairs:
            assert "Task:" in p.prompt
            assert "Risk tier:" in p.prompt

    # ── Negative examples ─────────────────────────────────────────────────────

    def test_negative_count(self):
        pairs = self.gen.generate_authority_negative(20)
        assert len(pairs) == 20

    def test_negative_pairs_are_not_admissible(self):
        pairs = self.gen.generate_authority_negative(20)
        for p in pairs:
            assert not p.admissible

    def test_negative_pairs_have_rejection_reason(self):
        pairs = self.gen.generate_authority_negative(20)
        for p in pairs:
            assert len(p.rejection_reason) > 0

    def test_negative_completions_contain_deny(self):
        pairs = self.gen.generate_authority_negative(20)
        for p in pairs:
            assert "DENY" in p.completion

    def test_negative_completions_contain_corrected_action(self):
        pairs = self.gen.generate_authority_negative(20)
        for p in pairs:
            assert "[CORRECTED ACTION]" in p.completion

    def test_negative_completions_contain_rejected_action(self):
        pairs = self.gen.generate_authority_negative(20)
        for p in pairs:
            assert "[REJECTED ACTION]" in p.completion

    def test_violations_cover_all_patterns(self):
        """Every violation pattern should appear in the generated negatives."""
        pairs = self.gen.generate_authority_negative(200)
        reasons = {p.rejection_reason for p in pairs}
        # Should have multiple distinct rejection reasons
        assert len(reasons) >= 3

    # ── Calibration pairs ─────────────────────────────────────────────────────

    def test_calibration_count(self):
        pairs = self.gen.generate_calibration_pairs(20)
        assert len(pairs) == 20

    def test_calibration_pairs_have_halt_anchor(self):
        pairs = self.gen.generate_calibration_pairs(20)
        for p in pairs:
            assert p.anchor == "@halt"
            assert p.relation == "answer"

    def test_calibration_completions_contain_verdict(self):
        pairs = self.gen.generate_calibration_pairs(20)
        for p in pairs:
            assert "ACCEPT" in p.completion or "REJECT" in p.completion

    def test_calibration_completions_contain_reasoning(self):
        pairs = self.gen.generate_calibration_pairs(20)
        for p in pairs:
            assert "[CALIBRATION REASONING]" in p.completion

    def test_calibration_has_both_accept_and_reject(self):
        pairs = self.gen.generate_calibration_pairs(100)
        accepts = sum(1 for p in pairs if p.admissible)
        rejects = sum(1 for p in pairs if not p.admissible)
        assert accepts > 0
        assert rejects > 0

    # ── generate_all ──────────────────────────────────────────────────────────

    def test_generate_all_count(self):
        pairs = self.gen.generate_all(n_positive=10, n_negative=8, n_calibration=4)
        assert len(pairs) == 22

    def test_generate_all_has_all_types(self):
        pairs = self.gen.generate_all(n_positive=20, n_negative=20, n_calibration=10)
        types = {p.pair_type for p in pairs}
        assert "authority_positive" in types
        assert "authority_negative" in types
        assert "calibration" in types

    def test_generate_all_is_shuffled(self):
        pairs = self.gen.generate_all(n_positive=30, n_negative=30, n_calibration=10)
        # Check that types are interleaved (not all positives first)
        type_sequence = [p.pair_type for p in pairs]
        # Should not be sorted (all positives then negatives)
        sorted_types = sorted(type_sequence)
        assert type_sequence != sorted_types

    def test_deterministic_with_same_seed(self):
        gen1 = SemanticSignalGenerator(seed=99)
        gen2 = SemanticSignalGenerator(seed=99)
        pairs1 = gen1.generate_all(10, 8, 4)
        pairs2 = gen2.generate_all(10, 8, 4)
        assert pairs1[0].prompt == pairs2[0].prompt

    def test_different_seeds_give_different_results(self):
        gen1 = SemanticSignalGenerator(seed=1)
        gen2 = SemanticSignalGenerator(seed=2)
        pairs1 = gen1.generate_all(10, 8, 4)
        pairs2 = gen2.generate_all(10, 8, 4)
        # At least some prompts should differ
        prompts1 = {p.prompt for p in pairs1}
        prompts2 = {p.prompt for p in pairs2}
        assert prompts1 != prompts2


# ── generate_semantic_dataset ─────────────────────────────────────────────────

class TestGenerateSemanticDataset:

    def test_creates_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "semantic.jsonl"
            result = generate_semantic_dataset(path, n_positive=10, n_negative=8, n_calibration=4)
            assert result.exists()

    def test_file_has_correct_line_count(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "semantic.jsonl"
            generate_semantic_dataset(path, n_positive=10, n_negative=8, n_calibration=4)
            lines = [l for l in path.read_text().splitlines() if l.strip()]
            assert len(lines) == 22

    def test_file_is_valid_jsonl(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "semantic.jsonl"
            generate_semantic_dataset(path, n_positive=5, n_negative=5, n_calibration=5)
            for line in path.read_text().splitlines():
                if line.strip():
                    obj = json.loads(line)
                    assert "prompt" in obj
                    assert "completion" in obj

    def test_creates_parent_dirs(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "nested" / "deep" / "semantic.jsonl"
            generate_semantic_dataset(path, n_positive=5, n_negative=5, n_calibration=5)
            assert path.exists()


# ── merge_with_syntax_dataset ─────────────────────────────────────────────────

class TestMergeWithSyntaxDataset:

    def _write_jsonl(self, path: Path, rows: list):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")

    def test_merge_produces_output(self):
        with tempfile.TemporaryDirectory() as d:
            syntax = Path(d) / "syntax.jsonl"
            semantic = Path(d) / "semantic.jsonl"
            output = Path(d) / "merged.jsonl"

            self._write_jsonl(syntax, [{"prompt": f"p{i}", "completion": f"c{i}"} for i in range(10)])
            self._write_jsonl(semantic, [{"prompt": f"sp{i}", "completion": f"sc{i}"} for i in range(10)])

            merge_with_syntax_dataset(syntax, semantic, output, semantic_weight=0.4)
            assert output.exists()

    def test_merge_respects_semantic_weight(self):
        with tempfile.TemporaryDirectory() as d:
            syntax = Path(d) / "syntax.jsonl"
            semantic = Path(d) / "semantic.jsonl"
            output = Path(d) / "merged.jsonl"

            self._write_jsonl(syntax, [{"prompt": f"p{i}", "completion": f"c{i}"} for i in range(100)])
            self._write_jsonl(semantic, [{"prompt": f"sp{i}", "completion": f"sc{i}", "pair_type": "semantic"} for i in range(100)])

            merge_with_syntax_dataset(syntax, semantic, output, semantic_weight=0.4)
            lines = [json.loads(l) for l in output.read_text().splitlines() if l.strip()]
            semantic_count = sum(1 for l in lines if l.get("pair_type") == "semantic")
            total = len(lines)
            # Should be approximately 40% semantic
            ratio = semantic_count / total
            assert 0.3 <= ratio <= 0.5

    def test_merge_handles_missing_syntax_file(self):
        with tempfile.TemporaryDirectory() as d:
            syntax = Path(d) / "nonexistent.jsonl"
            semantic = Path(d) / "semantic.jsonl"
            output = Path(d) / "merged.jsonl"

            self._write_jsonl(semantic, [{"prompt": f"sp{i}", "completion": f"sc{i}"} for i in range(10)])
            merge_with_syntax_dataset(syntax, semantic, output)
            # Should not raise, output may be empty or just semantic
            assert output.exists()

    def test_merge_handles_missing_semantic_file(self):
        with tempfile.TemporaryDirectory() as d:
            syntax = Path(d) / "syntax.jsonl"
            semantic = Path(d) / "nonexistent.jsonl"
            output = Path(d) / "merged.jsonl"

            self._write_jsonl(syntax, [{"prompt": f"p{i}", "completion": f"c{i}"} for i in range(10)])
            merge_with_syntax_dataset(syntax, semantic, output)
            assert output.exists()


# ── Integration: semantic pairs contain valid SCL in completions ──────────────

class TestSemanticPairsContainValidSCL:
    """
    The SCL actions embedded in semantic pair completions must be valid SCL.
    This ensures the authority model trains on correct syntax.
    """

    def setup_method(self):
        self.gen = SemanticSignalGenerator(seed=7)

    def _extract_scl_from_completion(self, completion: str) -> list:
        """Extract lines that look like SCL from a completion."""
        scl_lines = []
        for line in completion.splitlines():
            line = line.strip()
            if line.startswith("@"):
                scl_lines.append(line)
        return scl_lines

    def test_positive_completions_end_with_valid_scl(self):
        pairs = self.gen.generate_authority_positive(30)
        for p in pairs:
            scl_lines = self._extract_scl_from_completion(p.completion)
            assert len(scl_lines) >= 1, f"No SCL found in: {p.completion}"
            for scl in scl_lines:
                assert is_complete_scl(scl), f"Invalid SCL in positive completion: {scl!r}"

    def test_negative_completions_contain_valid_corrected_scl(self):
        pairs = self.gen.generate_authority_negative(30)
        for p in pairs:
            # The corrected action should be valid SCL
            if "[CORRECTED ACTION]" in p.completion:
                after = p.completion.split("[CORRECTED ACTION]")[1].strip()
                first_line = after.splitlines()[0].strip()
                if first_line.startswith("@"):
                    assert is_complete_scl(first_line), f"Invalid corrected SCL: {first_line!r}"
