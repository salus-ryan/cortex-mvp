"""Canonical SCL specification for Cortex.

This module is the single lightweight source of truth for the finite SCL action
algebra used by parser/schema/policy/verifier/decoder consistency tests.
It is not a proof assistant, but it makes drift mechanically detectable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RelationSpec:
    fields: tuple[str, ...] = ()
    required: tuple[str, ...] = ()


SCL_SPEC: dict[str, dict[str, RelationSpec]] = {
    "@state": {
        "update": RelationSpec(fields=("task_id", "phase", "confidence", "last_error", "failure", "suspect")),
        "snapshot": RelationSpec(fields=("task_id", "phase", "confidence")),
    },
    "@memory": {
        "read": RelationSpec(fields=("query",), required=("query",)),
        "write": RelationSpec(fields=("key", "value", "ttl"), required=("key", "value")),
        "compress": RelationSpec(fields=("source", "target", "max_tokens"), required=("source", "target")),
        "ignore": RelationSpec(fields=("reason",)),
    },
    "@budget": {
        "spend": RelationSpec(fields=("units", "reason"), required=("units", "reason")),
        "check": RelationSpec(),
        "snapshot": RelationSpec(),
    },
    "@verify": {
        "run": RelationSpec(fields=("type", "target"), required=("type", "target")),
        "assert": RelationSpec(fields=("type", "target"), required=("type", "target")),
    },
    "@tool": {
        "call": RelationSpec(fields=("name", "args", "target", "strategy", "risk"), required=("name",)),
        "deny": RelationSpec(fields=("name", "reason"), required=("name", "reason")),
    },
    "@repair": {
        "rollback": RelationSpec(fields=("artifact", "reason"), required=("artifact",)),
        "patch": RelationSpec(fields=("target", "strategy"), required=("target",)),
        "diagnose": RelationSpec(fields=("reason", "target")),
    },
    "@halt": {
        "answer": RelationSpec(fields=("status", "confidence", "evidence", "evidence_ref"), required=("status", "confidence", "evidence")),
        "fail": RelationSpec(fields=("status", "confidence", "evidence", "reason"), required=("status", "confidence", "evidence")),
        "defer": RelationSpec(fields=("status", "confidence", "evidence", "reason", "missing"), required=("status", "confidence", "evidence")),
    },
}

ANCHORS: tuple[str, ...] = tuple(SCL_SPEC)
RELATIONS: dict[str, tuple[str, ...]] = {anchor: tuple(relations) for anchor, relations in SCL_SPEC.items()}
REQUIRED_FIELDS: dict[tuple[str, str], tuple[str, ...]] = {
    (anchor, relation): spec.required
    for anchor, relations in SCL_SPEC.items()
    for relation, spec in relations.items()
}


def is_valid_pair(anchor: str, relation: str) -> bool:
    return relation in SCL_SPEC.get(anchor, {})


def required_fields(anchor: str, relation: str) -> tuple[str, ...]:
    return REQUIRED_FIELDS.get((anchor, relation), ())


def validate_fields(anchor: str, relation: str, fields: dict[str, Any]) -> tuple[bool, str]:
    if not is_valid_pair(anchor, relation):
        return False, f"invalid SCL pair: {anchor} → {relation}"
    missing = [f for f in required_fields(anchor, relation) if f not in fields]
    if missing:
        return False, f"missing required fields: {', '.join(missing)}"
    allowed = set(SCL_SPEC[anchor][relation].fields)
    unknown = [f for f in fields if allowed and f not in allowed]
    if unknown:
        return False, f"unknown fields: {', '.join(unknown)}"
    return True, "ok"
