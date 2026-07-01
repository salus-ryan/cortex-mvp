"""Tests for cortex/scl_corpus.py — SCL-native pretraining corpus generator."""
import json
import pytest
from pathlib import Path
import tempfile

from cortex.scl_corpus import (
    SCLCorpusGenerator, SCLStep, SCLTrajectory,
    ANCHORS, RELATIONS, TASK_FAMILIES, REASONING_PATTERNS,
)
from cortex.scl_parser import parse as scl_parse


@pytest.fixture
def gen():
    return SCLCorpusGenerator(seed=42)


# ── SCLStep ────────────────────────────────────────────────────────────────────

class TestSCLStep:
    def test_tool_call_emits_valid_scl(self, gen):
        step = SCLStep(
            anchor="@tool", relation="call",
            fields={"name": "bash", "args": "ls -la"},
            nl_annotation="list files",
        )
        scl = step.to_scl()
        result = scl_parse(scl)
        assert result.valid, f"Invalid SCL: {scl}"
        assert result.action.anchor == "@tool"
        assert result.action.relation == "call"

    def test_halt_answer_emits_valid_scl(self, gen):
        step = SCLStep(
            anchor="@halt", relation="answer",
            fields={"status": "complete", "confidence": 0.9, "evidence": "done"},
        )
        scl = step.to_scl()
        result = scl_parse(scl)
        assert result.valid, f"Invalid SCL: {scl}"
        assert result.action.anchor == "@halt"

    def test_memory_store_emits_valid_scl(self, gen):
        # Schema uses 'write' not 'store'
        step = SCLStep(
            anchor="@memory", relation="write",
            fields={"key": "result", "value": "success"},
        )
        scl = step.to_scl()
        result = scl_parse(scl)
        assert result.valid, f"Invalid SCL: {scl}"

    def test_verify_run_emits_valid_scl(self, gen):
        # @verify requires type and target fields
        step = SCLStep(
            anchor="@verify", relation="run",
            fields={"type": "schema", "target": "output.json"},
        )
        scl = step.to_scl()
        result = scl_parse(scl)
        assert result.valid, f"Invalid SCL: {scl}"

    def test_all_anchors_emit_parseable_scl(self, gen):
        """Every anchor/relation combination must produce parseable SCL."""
        for anchor, relations in RELATIONS.items():
            for relation in relations:
                step = gen._make_step(anchor, relation, "file_ops", ["bash"])
                scl = step.to_scl()
                result = scl_parse(scl)
                assert result.valid, f"Invalid SCL for {anchor} → {relation}: {scl!r}"


# ── SCLTrajectory ──────────────────────────────────────────────────────────────

class TestSCLTrajectory:
    def test_to_scl_context_includes_goal(self, gen):
        traj = gen.generate_trajectory("file_ops", n_steps=2)
        ctx = traj.to_scl_context()
        assert "GOAL:" in ctx
        assert "STEP[0]:" in ctx

    def test_to_scl_context_partial(self, gen):
        traj = gen.generate_trajectory("file_ops", n_steps=3)
        ctx = traj.to_scl_context(up_to_step=1)
        assert "STEP[0]:" in ctx
        assert "STEP[1]:" not in ctx

    def test_task_id_is_unique(self, gen):
        ids = {gen.generate_trajectory("file_ops").task_id for _ in range(20)}
        assert len(ids) == 20


# ── SCLCorpusGenerator ─────────────────────────────────────────────────────────

class TestSCLCorpusGenerator:
    def test_generate_trajectory_all_families(self, gen):
        for family in TASK_FAMILIES:
            traj = gen.generate_trajectory(family, n_steps=3)
            assert traj.task_family == family
            assert len(traj.steps) >= 1
            assert traj.goal_scl
            assert traj.goal_nl

    def test_generate_trajectory_steps_valid_scl(self, gen):
        for family in TASK_FAMILIES:
            traj = gen.generate_trajectory(family, n_steps=4)
            for step in traj.steps:
                scl = step.to_scl()
                result = scl_parse(scl)
                assert result.valid, f"Family {family}: invalid SCL {scl!r}"

    def test_continuation_example_structure(self, gen):
        traj = gen.generate_trajectory("code_exec", n_steps=3)
        ex = gen.make_continuation_example(traj, step_idx=1)
        assert ex["type"] == "continuation"
        assert "[SCL-CONTEXT]" in ex["prompt"]
        assert "[NEXT-TRANSITION]" in ex["prompt"]
        assert ex["completion"].strip()
        assert ex["scl_valid"]

    def test_continuation_example_out_of_bounds(self, gen):
        traj = gen.generate_trajectory("file_ops", n_steps=2)
        ex = gen.make_continuation_example(traj, step_idx=99)
        assert ex == {}

    def test_compression_example_structure(self, gen):
        traj = gen.generate_trajectory("search_answer", n_steps=2)
        ex = gen.make_compression_example(traj)
        assert ex["type"] == "compression"
        assert "[NL-INTENT]" in ex["prompt"]
        assert "[SCL-COMPRESSION]" in ex["prompt"]
        assert ex["completion"].strip()
        assert ex["scl_valid"]

    def test_reflection_example_success(self, gen):
        ex = gen.make_reflection_example("success")
        assert ex["type"] == "reflection"
        assert "[SCL-TRAJECTORY]" in ex["prompt"]
        assert "[OUTCOME]: success" in ex["prompt"]
        assert "[SCL-REFLECTION]" in ex["prompt"]
        assert ex["quality"] == 1.0

    def test_reflection_example_failure(self, gen):
        ex = gen.make_reflection_example("failure")
        assert ex["outcome"] == "failure"
        assert ex["quality"] < 1.0
        assert "policy_violation" in ex["completion"] or "repair" in ex["completion"]

    def test_reflection_example_partial(self, gen):
        ex = gen.make_reflection_example("partial")
        assert ex["outcome"] == "partial"
        assert 0.0 < ex["quality"] < 1.0

    def test_generate_corpus_ratios(self, gen):
        examples = gen.generate_corpus(n_total=100,
                                        continuation_ratio=0.60,
                                        compression_ratio=0.25,
                                        reflection_ratio=0.15)
        by_type = {}
        for ex in examples:
            t = ex["type"]
            by_type[t] = by_type.get(t, 0) + 1

        assert by_type.get("continuation", 0) >= 50
        assert by_type.get("compression", 0) >= 20
        assert by_type.get("reflection", 0) >= 10

    def test_generate_corpus_all_valid_scl(self, gen):
        examples = gen.generate_corpus(n_total=50)
        invalid = []
        for ex in examples:
            compl = ex.get("completion", "").strip()
            if compl:
                # Each line of completion should be valid SCL
                for line in compl.split("\n"):
                    line = line.strip()
                    if line and line.startswith("@"):
                        result = scl_parse(line)
                        if not result.valid:
                            invalid.append((ex["type"], line))
        assert len(invalid) == 0, f"Invalid SCL in corpus: {invalid[:3]}"

    def test_generate_and_save(self, gen, tmp_path):
        out = tmp_path / "corpus.jsonl"
        result = gen.generate_and_save(out, n_total=50)
        assert result["total"] == 50
        assert out.exists()
        lines = out.read_text().strip().split("\n")
        assert len(lines) == 50
        # Each line is valid JSON
        for line in lines:
            obj = json.loads(line)
            assert "prompt" in obj
            assert "completion" in obj
            assert "type" in obj

    def test_corpus_prompt_nl_is_annotation_not_substrate(self, gen):
        """
        In compression examples, NL is the input and SCL is the output.
        This verifies the epistemic priority: SCL is the substrate.
        """
        examples = gen.generate_corpus(n_total=50)
        compression = [e for e in examples if e["type"] == "compression"]
        assert len(compression) > 0
        for ex in compression:
            # NL is in the prompt (input side)
            assert "[NL-INTENT]" in ex["prompt"]
            # SCL is in the completion (output side — the substrate)
            assert "[SCL-COMPRESSION]" in ex["prompt"]
            compl = ex["completion"].strip()
            assert compl.startswith("@"), f"Completion should be SCL: {compl!r}"

    def test_continuation_scl_context_is_scl_not_nl(self, gen):
        """
        In continuation examples, the context is SCL, not NL.
        The model reasons in SCL space.
        """
        examples = gen.generate_corpus(n_total=50)
        continuation = [e for e in examples if e["type"] == "continuation"]
        assert len(continuation) > 0
        for ex in continuation:
            assert "[SCL-CONTEXT]" in ex["prompt"]
            # The context should contain SCL assertions
            assert "GOAL:" in ex["prompt"]
