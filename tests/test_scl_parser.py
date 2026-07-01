"""Tests for the SCL parser."""

import pytest
from cortex.scl_parser import parse, emit, SCLAction, ParseResult


class TestSCLParse:
    def test_parse_tool_call(self):
        scl = '@tool → call [name: "pytest", args: "tests/", risk: "verify"]'
        result = parse(scl)
        assert result.valid
        assert result.action.anchor == "@tool"
        assert result.action.relation == "call"
        assert result.action.fields["name"] == "pytest"
        assert result.action.fields["risk"] == "verify"

    def test_parse_arrow_ascii(self):
        scl = '@tool -> call [name: "pytest", args: "tests/", risk: "verify"]'
        result = parse(scl)
        assert result.valid

    def test_parse_state_update(self):
        scl = '@state → update [task_id: "T-001", phase: "diagnose", confidence: 0.62]'
        result = parse(scl)
        assert result.valid
        assert result.action.anchor == "@state"
        assert result.action.fields["phase"] == "diagnose"
        assert result.action.fields["confidence"] == pytest.approx(0.62)

    def test_parse_memory_read(self):
        scl = '@memory → read [query: "budget accounting invariant"]'
        result = parse(scl)
        assert result.valid
        assert result.action.anchor == "@memory"
        assert result.action.relation == "read"

    def test_parse_memory_write(self):
        scl = '@memory → write [key: "router.budget_rule", value: "debit before execution", ttl: "persistent"]'
        result = parse(scl)
        assert result.valid
        assert result.action.fields["ttl"] == "persistent"

    def test_parse_halt_answer(self):
        scl = '@halt → answer [status: "complete", confidence: 0.91, evidence: "tests passed"]'
        result = parse(scl)
        assert result.valid
        assert result.action.anchor == "@halt"
        assert result.action.fields["status"] == "complete"

    def test_parse_halt_fail(self):
        scl = '@halt → fail [status: "blocked", reason: "required tool unavailable"]'
        result = parse(scl)
        assert result.valid

    def test_parse_tool_deny(self):
        scl = '@tool → deny [reason: "destructive command outside allowed policy"]'
        result = parse(scl)
        assert result.valid

    def test_parse_repair_rollback(self):
        scl = '@repair → rollback [artifact: "patch_003", reason: "introduced regression"]'
        result = parse(scl)
        assert result.valid

    def test_parse_verify_run(self):
        scl = '@verify → run [type: "unit_test", target: "tests/test_budget.py"]'
        result = parse(scl)
        assert result.valid

    def test_parse_budget_spend(self):
        scl = '@budget → spend [units: 3, reason: "unit test required"]'
        result = parse(scl)
        assert result.valid
        assert result.action.fields["units"] == 3

    def test_parse_empty_fields(self):
        scl = '@budget → check []'
        result = parse(scl)
        assert result.valid

    def test_parse_no_fields(self):
        scl = '@budget → check'
        result = parse(scl)
        assert result.valid

    def test_parse_invalid_no_arrow(self):
        scl = '@tool call [name: "pytest"]'
        result = parse(scl)
        assert not result.valid
        assert "syntax error" in result.error.lower() or "pattern" in result.error.lower()

    def test_parse_empty_string(self):
        result = parse("")
        assert not result.valid
        assert "empty" in result.error.lower()

    def test_parse_invalid_anchor(self):
        scl = '@hardware → mutate [type: "memory"]'
        result = parse(scl)
        # Schema validation should fail for unknown/forbidden anchor
        assert not result.valid

    def test_parse_invalid_phase(self):
        scl = '@state → update [phase: "flying"]'
        result = parse(scl)
        assert not result.valid

    def test_parse_invalid_ttl(self):
        scl = '@memory → write [key: "x", value: "y", ttl: "forever"]'
        result = parse(scl)
        assert not result.valid

    def test_parse_invalid_relation_for_anchor(self):
        scl = '@state → fly [task_id: "T-001"]'
        result = parse(scl)
        assert not result.valid


class TestSCLEmit:
    def test_emit_basic(self):
        result = emit("@tool", "call", name="pytest", risk="verify")
        assert "@tool" in result
        assert "call" in result
        assert "pytest" in result

    def test_emit_no_fields(self):
        result = emit("@budget", "check")
        assert result == "@budget → check"

    def test_emit_numeric_field(self):
        result = emit("@budget", "spend", units=3)
        assert "units: 3" in result

    def test_emit_float_field(self):
        result = emit("@halt", "answer", confidence=0.91)
        assert "confidence: 0.91" in result

    def test_emit_roundtrip(self):
        original = '@tool → call [name: "pytest", args: "tests/", risk: "verify"]'
        parsed = parse(original)
        assert parsed.valid
        emitted = emit(
            parsed.action.anchor,
            parsed.action.relation,
            **parsed.action.fields,
        )
        re_parsed = parse(emitted)
        assert re_parsed.valid
        assert re_parsed.action.anchor == parsed.action.anchor
        assert re_parsed.action.relation == parsed.action.relation
