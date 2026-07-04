# Formal Transition Surface

`cortex/transition_spec.py` defines a total transition classification for every valid SCL pair from `cortex/scl_spec.py`.

The transition spec does not prove all behavior. It makes the runtime surface mechanically inspectable:

- every valid `@anchor → relation` pair has a transition class
- mutating vs non-mutating actions are explicit
- terminal halt actions are explicit
- file/memory/tool effects are identified
- minimal postconditions are checkable

Run:

```bash
python -m pytest tests/test_transition_spec.py -q
```

Current postcondition checks cover:

- transition table totality
- all SCL pairs require audit evidence by default
- non-negative action costs
- monotonic budget debits
- overspend refusal without unit mutation
- repair patch/rollback phase constraints
- tool provenance reference consistency
- runtime postcondition failures are audited and refused
- accepted runtime audit events include transition metadata

Roadmap:

1. Expand postconditions for every SCL pair.
2. Move runtime `_transition_state` to consume `transition_spec.py` directly.
3. Add property-generated action/state pairs.
4. Add file snapshot and rollback pre/postcondition checks.
5. Add audit event requirements per transition class.
