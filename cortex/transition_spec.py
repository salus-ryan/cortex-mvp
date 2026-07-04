"""Total transition specification for SCL runtime state effects.

This is a formal-core companion to ``scl_spec.py``. It classifies every valid
SCL pair by its expected runtime effect so missing transition semantics become a
test failure instead of silent drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cortex.scl_spec import RELATIONS


@dataclass(frozen=True)
class TransitionSpec:
    phase: str
    mutates_state: bool = False
    mutates_memory: bool = False
    mutates_files: bool = False
    external_tool: bool = False
    terminal: bool = False
    requires_audit: bool = True


TRANSITIONS: dict[tuple[str, str], TransitionSpec] = {
    ("@state", "update"): TransitionSpec("state", mutates_state=True),
    ("@state", "snapshot"): TransitionSpec("state"),
    ("@memory", "read"): TransitionSpec("memory", mutates_memory=False),
    ("@memory", "write"): TransitionSpec("memory", mutates_memory=True),
    ("@memory", "compress"): TransitionSpec("memory", mutates_memory=True),
    ("@memory", "ignore"): TransitionSpec("memory"),
    ("@budget", "spend"): TransitionSpec("budget", mutates_state=True),
    ("@budget", "check"): TransitionSpec("budget"),
    ("@budget", "snapshot"): TransitionSpec("budget"),
    ("@verify", "run"): TransitionSpec("verify", mutates_state=True, external_tool=True),
    ("@verify", "assert"): TransitionSpec("verify", mutates_state=True),
    ("@tool", "call"): TransitionSpec("act", mutates_state=True, external_tool=True),
    ("@tool", "deny"): TransitionSpec("deny"),
    ("@repair", "rollback"): TransitionSpec("repair", mutates_state=True, mutates_files=True),
    ("@repair", "patch"): TransitionSpec("repair", mutates_state=True, mutates_files=True),
    ("@repair", "diagnose"): TransitionSpec("repair", mutates_state=True),
    ("@halt", "answer"): TransitionSpec("halt", terminal=True),
    ("@halt", "fail"): TransitionSpec("halt", terminal=True),
    ("@halt", "defer"): TransitionSpec("halt", terminal=True),
}


def all_scl_pairs() -> set[tuple[str, str]]:
    return {(anchor, relation) for anchor, rels in RELATIONS.items() for relation in rels}


def missing_transition_pairs() -> set[tuple[str, str]]:
    return all_scl_pairs() - set(TRANSITIONS)


def transition_for(anchor: str, relation: str) -> TransitionSpec:
    return TRANSITIONS[(anchor, relation)]


def check_postconditions(before: dict[str, Any], after: dict[str, Any], anchor: str, relation: str) -> tuple[bool, str]:
    """Check minimal postconditions for a runtime state transition.

    These are intentionally conservative invariants rather than a full proof.
    They catch impossible terminal mutations and expected phase/provenance drift.
    """
    spec = transition_for(anchor, relation)
    if spec.terminal and after != before:
        return False, "terminal transitions should not mutate runtime state before halt return"
    if (anchor, relation) == ("@repair", "rollback") and after.get("phase") != "repair":
        return False, "rollback must enter repair phase"
    if (anchor, relation) == ("@repair", "patch") and after.get("phase") != "verify":
        return False, "patch must enter verify phase"
    if anchor == "@verify" and relation == "run" and after.get("last_verify") not in {"passed", "failed", None}:
        return False, "verify transition must record passed/failed when it records last_verify"
    if anchor == "@tool" and spec.external_tool and after.get("latest_evidence_ref") and "evidence_provenance" not in after:
        return False, "tool evidence ref requires provenance map"
    return True, "ok"
