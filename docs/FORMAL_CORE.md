# Cortex Formal Core

Cortex is not mathematically complete in the proof-assistant sense. This document defines the parts that are finite, typed, and mechanically checked today.

## 1. Finite SCL algebra

The canonical lightweight SCL specification lives in:

```text
cortex/scl_spec.py
```

It defines:

```text
Anchor × Relation × Fields
```

Current anchors:

```text
@state
@memory
@budget
@verify
@tool
@repair
@halt
```

The consistency test suite verifies that this spec agrees with:

- `cortex/policy.py`
- `cortex/verifier.py`
- `cortex/constrained_decoder.py`
- `cortex/scl_schema.json`

Test:

```bash
python -m pytest tests/test_scl_spec_consistency.py -q
```

## 2. Runtime transition discipline

A Cortex step has this shape:

```text
proposal -> parse -> policy -> verifier -> execute -> debit -> score -> transition -> audit
```

The model is not authority. It proposes one SCL record. Runtime gates decide whether it is accepted, denied, repaired, or halted.

## 3. Bounded resources

Budget state is explicit:

- unit budget
- tool call budget
- step budget
- wall-clock budget

No runtime loop should be unbounded.

## 4. Evidence and audit

Cortex now has two evidence mechanisms:

1. `evidence_ref` provenance IDs for verified/tool outputs.
2. Hash-chained audit events in `cortex/audit_sink.py`.

Audit verification detects local modification, removal, or reordering of events.

## 5. What is not proven yet

Cortex does not yet prove:

- all allowed actions preserve all invariants
- shell/tool isolation is complete
- every runtime path has a total transition proof
- every state mutation has a formal pre/postcondition
- immune classification is complete or calibrated

## 6. Roadmap toward stronger formal completeness

1. Generate parser/schema/decoder/policy constants directly from `scl_spec.py`.
2. Define a total state transition table for every SCL pair.
3. Add pre/postconditions for each transition.
4. Add property tests for budget monotonicity and audit chain continuity.
5. Replace shell-like tools with command-specific typed tools.
6. Add invariant proofs/checks for memory, rollback, halt, and tool boundaries.

The practical goal is not mystical AGI. It is a governed agent substrate with a finite, inspectable action algebra and mechanically checked safety invariants.
