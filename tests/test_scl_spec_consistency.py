import json
from pathlib import Path

from cortex import constrained_decoder, policy, verifier
from cortex.scl_spec import ANCHORS, RELATIONS, REQUIRED_FIELDS, SCL_SPEC, validate_fields


def test_scl_spec_matches_policy_relations():
    assert set(policy._ALLOWED_ANCHORS) == set(ANCHORS)
    for anchor, relations in RELATIONS.items():
        assert set(policy._ALLOWED_RELATIONS[anchor]) == set(relations)


def test_scl_spec_matches_verifier_relations():
    assert verifier._ALLOWED_ANCHORS == set(ANCHORS)
    for anchor, relations in RELATIONS.items():
        assert verifier._ALLOWED_RELATIONS[anchor] == set(relations)


def test_scl_spec_matches_json_schema_relations():
    schema = json.loads((Path("cortex") / "scl_schema.json").read_text())
    assert set(schema["properties"]["anchor"]["enum"]) == set(ANCHORS)
    for block in schema["allOf"]:
        anchor = block["if"]["properties"]["anchor"]["const"]
        rels = block["then"]["properties"]["relation"]["enum"]
        assert set(rels) == set(RELATIONS[anchor])


def test_scl_spec_matches_constrained_decoder_relations():
    assert set(constrained_decoder.ANCHORS) == set(ANCHORS)
    for anchor, relations in RELATIONS.items():
        assert set(constrained_decoder.ANCHOR_RELATIONS[anchor]) == set(relations)


def test_scl_spec_required_fields_are_decoder_subset_for_shared_pairs():
    for pair, required in REQUIRED_FIELDS.items():
        decoder_required = tuple(constrained_decoder.REQUIRED_FIELDS[pair])
        assert set(decoder_required) == set(required)


def test_scl_spec_validate_fields_accepts_and_rejects():
    ok, reason = validate_fields("@tool", "call", {"name": "pytest", "risk": "verify"})
    assert ok, reason
    ok, reason = validate_fields("@tool", "call", {"risk": "verify"})
    assert not ok
    assert "missing" in reason
    ok, reason = validate_fields("@tool", "launch", {"name": "pytest"})
    assert not ok
    assert "invalid" in reason
