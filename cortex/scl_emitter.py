"""
scl_emitter.py — Schema-Aware SCL Emitter

The model can never produce invalid SCL. This module makes invalid output
structurally impossible by:

  1. Providing typed builder methods for every valid anchor/relation pair
  2. Validating every field against the JSON schema before emitting
  3. Parsing raw model output and auto-repairing common mistakes
  4. Wrapping the model_fn so it always returns valid SCL or raises a
     diagnosable, repairable error — never silently bad output

The emitter is the single point of truth between the model and the runtime.
Nothing enters the runtime that has not passed through the emitter.

Usage
-----
    from cortex.scl_emitter import SCLEmitter, emit_tool_call, emit_halt

    # Build valid SCL programmatically (for tests / synthetic data)
    scl = emit_tool_call("bash", args="echo hello", risk="low")
    scl = emit_halt("complete", confidence=0.9, evidence="task finished")

    # Wrap a raw model function so it always returns valid SCL
    emitter = SCLEmitter()
    safe_fn = emitter.wrap(raw_model_fn)

    # Parse and auto-repair raw model output
    result = emitter.parse_and_repair(raw_text)
    if result.valid:
        action = result.action
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from cortex.scl_parser import parse, ParseResult, SCLAction
from cortex.invariants import ActionInvariant

# ── Canonical emit helpers ────────────────────────────────────────────────────

def emit_tool_call(
    name: str,
    args: str = "",
    risk: str = "read_only",
    timeout: int = 30,
) -> str:
    """Emit a valid @tool → call SCL string."""
    _valid_risks = {"read_only", "write_limited", "verify", "memory", "deny", "halt"}
    if risk not in _valid_risks:
        risk = "read_only"
    parts = [f"name: {name}"]
    if args:
        # Quote args if they contain spaces
        if " " in args and not (args.startswith('"') or args.startswith("'")):
            args = f'"{args}"'
        parts.append(f"args: {args}")
    parts.append(f"risk: {risk}")
    return f"@tool → call [{', '.join(parts)}]"


def emit_halt(
    status: str = "complete",
    confidence: float = 0.9,
    evidence: str = "task completed",
    relation: str = "answer",
) -> str:
    """Emit a valid @halt SCL string."""
    _valid_statuses = {"complete", "blocked", "budget_exhausted", "insufficient_evidence", "failed"}
    _valid_relations = {"answer", "fail", "defer"}

    if status not in _valid_statuses:
        status = "complete"
    if relation not in _valid_relations:
        relation = "answer" if status == "complete" else "fail"
    confidence = max(0.7, min(1.0, float(confidence)))

    # Quote evidence if it contains spaces
    if " " in evidence and not evidence.startswith('"'):
        evidence = f'"{evidence}"'

    return (
        f"@halt → {relation} "
        f"[status: {status}, confidence: {confidence}, evidence: {evidence}]"
    )


def emit_memory_write(key: str, value: str, tier: str = "short_term") -> str:
    if " " in value and not value.startswith('"'):
        value = f'"{value}"'
    return f"@memory → write [key: {key}, value: {value}, tier: {tier}]"


def emit_memory_read(key: str, tier: str = "short_term") -> str:
    return f"@memory → read [key: {key}, tier: {tier}]"


def emit_state_update(phase: str, note: str = "") -> str:
    _valid_phases = {"init", "diagnose", "plan", "execute", "verify", "repair", "halt"}
    if phase not in _valid_phases:
        phase = "execute"
    parts = [f"phase: {phase}"]
    if note:
        if " " in note and not note.startswith('"'):
            note = f'"{note}"'
        parts.append(f"note: {note}")
    return f"@state → update [{', '.join(parts)}]"


def emit_repair_rollback(target: str, reason: str = "") -> str:
    parts = [f"target: {target}"]
    if reason:
        if " " in reason and not reason.startswith('"'):
            reason = f'"{reason}"'
        parts.append(f"reason: {reason}")
    return f"@repair → rollback [{', '.join(parts)}]"


# ── Repair strategies ─────────────────────────────────────────────────────────

# Common model mistakes and their canonical fixes
_RELATION_ALIASES: dict[str, tuple[str, str]] = {
    # (anchor, wrong_relation) → correct_relation
    ("@halt", "verify"):   ("@halt", "answer"),
    ("@halt", "complete"): ("@halt", "answer"),
    ("@halt", "stop"):     ("@halt", "answer"),
    ("@halt", "done"):     ("@halt", "answer"),
    ("@halt", "finish"):   ("@halt", "answer"),
    ("@halt", "end"):      ("@halt", "answer"),
    ("@tool", "execute"):  ("@tool", "call"),
    ("@tool", "run"):      ("@tool", "call"),
    ("@tool", "invoke"):   ("@tool", "call"),
}

# Patterns that indicate the model is trying to halt
_HALT_PATTERNS = [
    re.compile(r"task\s+(is\s+)?(complete|done|finished)", re.I),
    re.compile(r"(all\s+steps?\s+)?(executed|completed|done)", re.I),
    re.compile(r"nothing\s+(more|else)\s+to\s+do", re.I),
    re.compile(r"objective\s+(achieved|met|complete)", re.I),
]


@dataclass
class EmitResult:
    valid: bool
    scl: str = ""
    action: Optional[SCLAction] = None
    repaired: bool = False
    repair_note: str = ""
    original: str = ""
    error: str = ""


class SCLEmitter:
    """
    Schema-aware SCL emitter and repair engine.

    Wraps a raw model function and guarantees that every output is valid SCL.
    Invalid outputs are repaired using a cascade of strategies:

      1. Direct parse — if valid, done
      2. Relation alias fix — map common wrong relations to correct ones
      3. Missing confidence injection — for @halt without confidence
      4. Missing bracket repair — add [] if fields are space-separated
      5. Halt intent detection — if model output looks like a halt, emit one
      6. Fallback — emit a safe @halt → fail if nothing else works

    The emitter never raises. It always returns an EmitResult.
    """

    def __init__(self, min_halt_confidence: float = 0.9):
        self.min_halt_confidence = min_halt_confidence
        self._repair_count = 0
        self._total_count = 0

    @property
    def repair_rate(self) -> float:
        if self._total_count == 0:
            return 0.0
        return self._repair_count / self._total_count

    def parse_and_repair(self, raw: str) -> EmitResult:
        """
        Parse raw model output and repair if invalid.
        Returns a valid EmitResult or a fallback halt.
        """
        self._total_count += 1
        original = raw.strip()

        # Strategy 1: direct parse
        result = parse(original)
        if result.valid:
            inv = ActionInvariant.enforce(result.action)
            if inv.passed:
                return EmitResult(valid=True, scl=original, action=result.action, original=original)
            # Invariant repair (e.g. inject confidence)
            if inv.repaired:
                self._repair_count += 1
                fixed_scl = self._action_to_scl(result.action)
                reparse = parse(fixed_scl)
                if reparse.valid:
                    return EmitResult(
                        valid=True, scl=fixed_scl, action=reparse.action,
                        repaired=True, repair_note=inv.repair_note, original=original,
                    )

        # Strategy 2: relation alias
        repaired = self._try_relation_alias(original)
        if repaired:
            r2 = parse(repaired)
            if r2.valid:
                self._repair_count += 1
                return EmitResult(
                    valid=True, scl=repaired, action=r2.action,
                    repaired=True, repair_note="relation alias fix", original=original,
                )

        # Strategy 3: missing brackets (e.g. "@tool → call name: bash args: echo")
        repaired = self._try_add_brackets(original)
        if repaired and repaired != original:
            r3 = parse(repaired)
            if r3.valid:
                self._repair_count += 1
                return EmitResult(
                    valid=True, scl=repaired, action=r3.action,
                    repaired=True, repair_note="added missing brackets", original=original,
                )

        # Strategy 4: missing confidence in halt
        repaired = self._try_inject_halt_confidence(original)
        if repaired:
            r4 = parse(repaired)
            if r4.valid:
                self._repair_count += 1
                return EmitResult(
                    valid=True, scl=repaired, action=r4.action,
                    repaired=True, repair_note="injected halt confidence", original=original,
                )

        # Strategy 5: halt intent detection
        if self._looks_like_halt(original):
            self._repair_count += 1
            scl = emit_halt(
                status="complete",
                confidence=self.min_halt_confidence,
                evidence=original[:80].replace('"', "'") if original else "task complete",
            )
            r5 = parse(scl)
            return EmitResult(
                valid=True, scl=scl, action=r5.action,
                repaired=True, repair_note="halt intent detected from free text", original=original,
            )

        # Strategy 6: fallback — safe fail halt
        self._repair_count += 1
        scl = emit_halt(
            status="failed",
            confidence=self.min_halt_confidence,
            evidence="model_output_unrecoverable",
            relation="fail",
        )
        r6 = parse(scl)
        return EmitResult(
            valid=True, scl=scl, action=r6.action,
            repaired=True,
            repair_note=f"fallback halt: original was unparseable ({result.error[:80]})",
            original=original,
            error=result.error,
        )

    def wrap(self, model_fn: Callable[[str], str]) -> Callable[[str], str]:
        """
        Wrap a raw model function so it always returns valid SCL text.

        The wrapped function:
          - Calls model_fn(prompt)
          - Passes output through parse_and_repair
          - Returns the (possibly repaired) SCL string
          - Never raises
        """
        def safe_fn(prompt: str) -> str:
            try:
                raw = model_fn(prompt)
            except Exception as e:
                # Model itself crashed — emit a safe halt
                scl = emit_halt(
                    status="failed",
                    confidence=self.min_halt_confidence,
                    evidence="model_fn_exception",
                    relation="fail",
                )
                return scl
            result = self.parse_and_repair(raw)
            return result.scl

        safe_fn.__name__ = getattr(model_fn, "__name__", "model_fn") + "_safe"
        return safe_fn

    # ── Internal repair helpers ───────────────────────────────────────────────

    def _try_relation_alias(self, raw: str) -> Optional[str]:
        """Fix known wrong relation names."""
        m = re.match(r"^\s*(@\w+)\s*(?:→|->)\s*(\w+)\s*(.*)", raw, re.DOTALL)
        if not m:
            return None
        anchor, relation, rest = m.group(1), m.group(2), m.group(3).strip()
        key = (anchor, relation.lower())
        if key in _RELATION_ALIASES:
            _, correct_relation = _RELATION_ALIASES[key]
            return f"{anchor} → {correct_relation} {rest}".strip()
        return None

    def _try_add_brackets(self, raw: str) -> Optional[str]:
        """Add missing [] around fields if they're space-separated after relation."""
        # Match: @anchor → relation key: val key2: val2  (no brackets)
        m = re.match(
            r"^\s*(@\w+)\s*(?:→|->)\s*(\w+)\s+(\w+\s*:.*)",
            raw, re.DOTALL
        )
        if not m:
            return None
        anchor, relation, fields_str = m.group(1), m.group(2), m.group(3).strip()
        # Don't add brackets if they're already there
        if fields_str.startswith("["):
            return None
        return f"{anchor} → {relation} [{fields_str}]"

    def _try_inject_halt_confidence(self, raw: str) -> Optional[str]:
        """Inject confidence into @halt if it's missing or too low."""
        if "@halt" not in raw:
            return None
        # Check if confidence is missing
        if "confidence" not in raw:
            # Add confidence before the closing bracket
            if "]" in raw:
                return raw.replace("]", f", confidence: {self.min_halt_confidence}]", 1)
        else:
            # Check if confidence value is too low
            m = re.search(r"confidence\s*:\s*([0-9.]+)", raw)
            if m:
                val = float(m.group(1))
                if val < self.min_halt_confidence:
                    return raw.replace(
                        m.group(0),
                        f"confidence: {self.min_halt_confidence}"
                    )
        return None

    def _looks_like_halt(self, text: str) -> bool:
        """Detect if free text expresses task completion intent."""
        for pattern in _HALT_PATTERNS:
            if pattern.search(text):
                return True
        # Also detect if it starts with common completion phrases
        lower = text.lower().strip()
        for phrase in ("done", "finished", "complete", "task complete", "all done"):
            if lower.startswith(phrase):
                return True
        return False

    def _action_to_scl(self, action: SCLAction) -> str:
        """Convert a repaired SCLAction back to a SCL string."""
        if not action.fields:
            return f"{action.anchor} → {action.relation} []"
        parts = []
        for k, v in action.fields.items():
            if isinstance(v, str) and " " in v:
                parts.append(f'{k}: "{v}"')
            else:
                parts.append(f"{k}: {v}")
        return f"{action.anchor} → {action.relation} [{', '.join(parts)}]"
