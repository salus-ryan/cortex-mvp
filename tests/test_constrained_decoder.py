"""Tests for constrained_decoder.py — SCL grammar and greedy decoder."""

import pytest
from cortex.constrained_decoder import (
    SCLGrammar,
    GreedySCLDecoder,
    is_valid_scl_prefix,
    is_complete_scl,
    next_valid_chars,
    ANCHORS,
    ANCHOR_RELATIONS,
)


# ── Grammar state tests ───────────────────────────────────────────────────────

class TestSCLGrammar:

    def setup_method(self):
        self.g = SCLGrammar()

    def test_empty_string_starts_with_at(self):
        state, chars = self.g.state_and_continuations("")
        assert state == "start"
        assert "@" in chars

    def test_at_sign_leads_to_anchor_chars(self):
        state, chars = self.g.state_and_continuations("@")
        assert state == "in_anchor"
        # All anchors start with @t, @m, @h, @r, @s, @v, @b
        assert len(chars) > 0

    def test_partial_anchor_tool(self):
        state, chars = self.g.state_and_continuations("@t")
        assert state == "in_anchor"
        assert "o" in chars  # @tool

    def test_full_anchor_leads_to_space(self):
        state, chars = self.g.state_and_continuations("@tool")
        assert state == "after_anchor"
        assert " " in chars

    def test_arrow_continuation(self):
        state, chars = self.g.state_and_continuations("@tool →")
        # After arrow we expect space before relation
        assert state in ("in_arrow", "after_arrow", "after_anchor")

    def test_full_arrow_leads_to_relation_chars(self):
        state, chars = self.g.state_and_continuations("@tool → ")
        assert state == "after_arrow"
        assert len(chars) > 0

    def test_partial_relation(self):
        state, chars = self.g.state_and_continuations("@tool → c")
        assert state == "in_relation"
        assert "a" in chars  # "call"

    def test_full_relation_tool_call(self):
        state, chars = self.g.state_and_continuations("@tool → call")
        assert state in ("after_relation", "in_fields", "complete")

    def test_in_fields_allows_all(self):
        state, chars = self.g.state_and_continuations("@tool → call [name: ")
        assert state == "in_fields"
        assert chars is None  # free-form

    def test_invalid_anchor_is_error(self):
        state, chars = self.g.state_and_continuations("@xyz")
        assert state == "error"
        assert len(chars) == 0

    def test_all_anchors_are_valid_prefixes(self):
        for anchor in ANCHORS:
            state, _ = self.g.state_and_continuations(anchor)
            assert state != "error", f"Anchor {anchor} should not be error"

    def test_all_anchor_relations_are_valid(self):
        for anchor, relations in ANCHOR_RELATIONS.items():
            for rel in relations:
                partial = f"{anchor} → {rel}"
                state, _ = self.g.state_and_continuations(partial)
                assert state not in ("error",), f"{partial} should not be error"

    def test_is_valid_complete_halt(self):
        scl = '@halt → answer [status: "complete", confidence: 0.9, evidence: "done"]'
        assert self.g.is_valid_complete(scl)

    def test_is_valid_complete_tool_call(self):
        scl = '@tool → call [name: "bash", args: "ls"]'
        assert self.g.is_valid_complete(scl)

    def test_is_not_valid_complete_prose(self):
        assert not self.g.is_valid_complete("This is not SCL")

    def test_is_not_valid_complete_partial(self):
        assert not self.g.is_valid_complete("@tool → ")

    def test_cache_is_consistent(self):
        partial = "@tool → call"
        r1 = self.g.state_and_continuations(partial)
        r2 = self.g.state_and_continuations(partial)
        assert r1 == r2


# ── is_valid_scl_prefix ───────────────────────────────────────────────────────

class TestPrefixValidator:

    def test_empty_is_valid_prefix(self):
        assert is_valid_scl_prefix("")

    def test_at_is_valid_prefix(self):
        assert is_valid_scl_prefix("@")

    def test_anchor_is_valid_prefix(self):
        assert is_valid_scl_prefix("@tool")

    def test_complete_scl_is_valid_prefix(self):
        assert is_valid_scl_prefix('@tool → call [name: "bash", args: "ls"]')

    def test_invalid_anchor_is_not_valid_prefix(self):
        assert not is_valid_scl_prefix("@xyz_invalid")

    def test_prose_is_not_valid_prefix(self):
        assert not is_valid_scl_prefix("please run bash")


# ── is_complete_scl ───────────────────────────────────────────────────────────

class TestCompleteValidator:

    def test_halt_answer_is_complete(self):
        assert is_complete_scl('@halt → answer [status: "complete", confidence: 0.9, evidence: "done"]')

    def test_tool_call_is_complete(self):
        assert is_complete_scl('@tool → call [name: "bash", args: "ls"]')

    def test_memory_read_is_complete(self):
        assert is_complete_scl('@memory → read [tier: "episodic", query: "context"]')

    def test_partial_is_not_complete(self):
        assert not is_complete_scl("@tool → ")

    def test_prose_is_not_complete(self):
        assert not is_complete_scl("run bash ls")


# ── next_valid_chars ──────────────────────────────────────────────────────────

class TestNextValidChars:

    def test_empty_returns_at(self):
        chars = next_valid_chars("")
        assert "@" in chars

    def test_after_anchor_returns_space(self):
        chars = next_valid_chars("@tool")
        assert " " in chars

    def test_invalid_returns_empty(self):
        chars = next_valid_chars("@xyz_bad")
        assert len(chars) == 0


# ── GreedySCLDecoder ──────────────────────────────────────────────────────────

class TestGreedySCLDecoder:

    def setup_method(self):
        self.decoder = GreedySCLDecoder()

    def test_complete_prompt_returns_halt(self):
        result = self.decoder.decode("task is complete and done")
        assert result.startswith("@halt")
        assert "answer" in result

    def test_fail_prompt_returns_halt_fail(self):
        result = self.decoder.decode("I cannot complete this task, error occurred")
        assert result.startswith("@halt")
        assert "fail" in result

    def test_read_prompt_returns_memory_read(self):
        result = self.decoder.decode("retrieve previous context from memory")
        assert result.startswith("@memory")
        assert "read" in result

    def test_write_prompt_returns_memory_write(self):
        result = self.decoder.decode("store this observation in memory")
        assert result.startswith("@memory")
        assert "write" in result

    def test_rollback_prompt_returns_repair(self):
        result = self.decoder.decode("undo the last action and rollback")
        assert result.startswith("@repair")
        assert "rollback" in result

    def test_verify_prompt_returns_verify(self):
        result = self.decoder.decode("verify and check the current state")
        assert result.startswith("@verify")

    def test_budget_prompt_returns_budget(self):
        result = self.decoder.decode("check remaining budget and cost")
        assert result.startswith("@budget")

    def test_default_returns_tool_call(self):
        result = self.decoder.decode("list files in the directory")
        assert result.startswith("@tool")
        assert "call" in result

    def test_all_outputs_are_valid_scl(self):
        """Every output from the greedy decoder must be valid SCL."""
        prompts = [
            "complete the task",
            "I cannot proceed",
            "retrieve context",
            "save this to memory",
            "undo last action",
            "check the state",
            "how much budget remains",
            "run bash ls",
        ]
        for prompt in prompts:
            result = self.decoder.decode(prompt)
            assert is_complete_scl(result), f"Decoder output not valid SCL: {result!r} for prompt: {prompt!r}"

    def test_bash_tool_in_prompt(self):
        result = self.decoder.decode("run bash command to list files")
        assert "bash" in result

    def test_search_tool_in_prompt(self):
        result = self.decoder.decode("search for information about calibration")
        assert "search" in result
