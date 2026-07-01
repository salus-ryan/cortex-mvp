#!/usr/bin/env python3
"""Smoke-test for chat.py — no interactive input needed."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.makedirs("data", exist_ok=True)

from scripts.chat import nl_to_scl, render_response, _make_registry
from cortex.policy import Policy
from cortex.verifier import Verifier
from cortex.budget import Budget

policy   = Policy()
verifier = Verifier()
budget   = Budget()
registry = _make_registry()

# (input, expected_anchor, expected_relation, response_must_contain)
CASES = [
    ("list files in current directory",  "@tool",   "call",   None),
    ("run echo hello world",             "@tool",   "call",   None),
    ("remember my name is Ryan",         "@memory", "write",  None),
    ("recall my name",                   "@memory", "read",   None),
    # Destructive → halt-fail with denial evidence (policy ALLOWS the halt, response is a refusal)
    ("delete everything in /",           "@halt",   "fail",   "destructive"),
    ("task is complete",                 "@halt",   "answer", None),
    ("search for python tutorials",      "@tool",   "call",   None),
    ("I cannot do this",                 "@halt",   "fail",   None),
]

passed = failed = 0
print()
for text, exp_anchor, exp_relation, resp_contains in CASES:
    action   = nl_to_scl(text)
    pol      = policy.check(action, budget, registry)
    ver      = verifier.check_action(action, budget, registry)
    response = render_response(action, None)

    anchor_ok   = action.anchor   == exp_anchor
    relation_ok = action.relation == exp_relation
    policy_ok   = pol.allowed
    verifier_ok = ver.passed
    resp_ok     = (resp_contains is None) or (resp_contains.lower() in response.lower())

    ok   = anchor_ok and relation_ok and policy_ok and verifier_ok and resp_ok
    mark = "PASS" if ok else "FAIL"
    if ok:
        passed += 1
    else:
        failed += 1

    print(f"  [{mark}] {action.anchor} → {action.relation}  |  {text!r}")
    if not anchor_ok:
        print(f"         anchor: expected={exp_anchor}, got={action.anchor}")
    if not relation_ok:
        print(f"         relation: expected={exp_relation}, got={action.relation}")
    if not policy_ok:
        print(f"         policy denied: {pol.reason}")
    if not verifier_ok:
        print(f"         verifier blocked: {ver.reason}")
    if not resp_ok:
        print(f"         response missing '{resp_contains}': got={response!r}")

print()
print(f"  {passed}/{passed+failed} passed")
if failed:
    sys.exit(1)
