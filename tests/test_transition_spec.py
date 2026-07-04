from cortex.budget import Budget, BudgetExhaustedError
from cortex.runtime import CortexRuntime
from cortex.scl_parser import SCLAction
from cortex.tool_registry import ExecutionResult
from cortex.transition_spec import TRANSITIONS, all_scl_pairs, audit_required_pairs, check_postconditions, missing_transition_pairs
from cortex.verifier import VerifyResult, _action_cost


def test_transition_spec_is_total_for_scl_pairs():
    assert missing_transition_pairs() == set()
    assert set(TRANSITIONS) == all_scl_pairs()


def test_all_scl_pairs_require_audit_by_default():
    assert audit_required_pairs() == all_scl_pairs()


def test_non_tool_action_costs_are_non_negative_for_all_pairs():
    for anchor, relation in all_scl_pairs():
        action = SCLAction(anchor=anchor, relation=relation, fields={})
        assert _action_cost(action) >= 0


def test_budget_debits_are_monotonic():
    budget = Budget(max_units=10, max_tool_calls=3, max_steps=5)
    before = budget.remaining_units
    budget.debit(3, reason="test")
    assert budget.remaining_units == before - 3
    assert budget.used_units == 3
    budget.debit_step()
    assert budget.remaining_steps == 4


def test_budget_refuses_overspend_without_changing_units():
    budget = Budget(max_units=2)
    try:
        budget.debit(3, reason="overspend")
    except BudgetExhaustedError:
        pass
    else:
        raise AssertionError("expected BudgetExhaustedError")
    assert budget.remaining_units == 2
    assert budget.used_units == 0


def test_runtime_transition_postconditions_for_repair_patch(tmp_path):
    rt = CortexRuntime(model_fn=lambda _: "@budget → check []", workspace=str(tmp_path), store=None)
    before = {"phase": "execute"}
    action = SCLAction(anchor="@repair", relation="patch", fields={"target": "x.py"})
    after = rt._transition_state(before, action, ExecutionResult("repair.patch", True, "ok"), VerifyResult(True))
    ok, reason = check_postconditions(before, after, "@repair", "patch")
    assert ok, reason


def test_runtime_transition_postconditions_for_tool_provenance(tmp_path):
    rt = CortexRuntime(model_fn=lambda _: "@budget → check []", workspace=str(tmp_path), store=None)
    action = SCLAction(anchor="@tool", relation="call", fields={"name": "shell.readonly"}, raw='@tool → call [name: "shell.readonly"]')
    after = rt._transition_state({}, action, ExecutionResult("shell.readonly", True, "ok"), VerifyResult(True))
    ok, reason = check_postconditions({}, after, "@tool", "call")
    assert ok, reason
    assert after["latest_evidence_ref"] in after["evidence_provenance"]
