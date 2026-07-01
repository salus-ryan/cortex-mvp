"""
scl_parser.py — Cortex SCL (Semantic Compression Language) Parser

Parses SCL control records of the form:
    @anchor → relation [key: value, key2: value2]

Returns a structured ParseResult with validity flag, parsed action, and error details.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import jsonschema

_SCHEMA_PATH = Path(__file__).parent / "scl_schema.json"
_SCHEMA: dict = json.loads(_SCHEMA_PATH.read_text())

# Regex for the top-level SCL structure
# Supports both → (U+2192) and -> as the relation operator
_SCL_RE = re.compile(
    r"^\s*(?P<anchor>@\w+)\s*(?:→|->)\s*(?P<relation>\w+)\s*(?:\[(?P<fields>[^\]]*)\])?\s*$",
    re.UNICODE,
)

# Regex to parse individual key: value pairs inside [ ... ]
# Values may be quoted strings, numbers, or bare identifiers
_FIELD_RE = re.compile(
    r"""(?P<key>\w+)\s*:\s*(?P<value>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|-?\d+(?:\.\d+)?|\w[\w.\-/]*)""",
    re.UNICODE,
)


@dataclass
class SCLAction:
    """Parsed, validated SCL control record."""

    anchor: str
    relation: str
    fields: dict[str, Any] = field(default_factory=dict)
    raw: str = ""

    def to_dict(self) -> dict:
        return {"anchor": self.anchor, "relation": self.relation, "fields": self.fields, "raw": self.raw}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


@dataclass
class ParseResult:
    """Result of parsing an SCL string."""

    valid: bool
    action: Optional[SCLAction] = None
    error: str = ""
    raw: str = ""

    @property
    def invalid(self) -> bool:
        return not self.valid


def _coerce_value(v: str) -> Any:
    """Convert a raw string token to a typed Python value."""
    # Strip surrounding quotes
    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
        return v[1:-1]
    # Boolean
    if v.lower() == "true":
        return True
    if v.lower() == "false":
        return False
    # Numeric
    try:
        if "." in v:
            return float(v)
        return int(v)
    except ValueError:
        pass
    return v


def parse(text: str) -> ParseResult:
    """
    Parse a single SCL control record string.

    Args:
        text: Raw SCL string, e.g. '@tool → call [name: "pytest", risk: "verify"]'

    Returns:
        ParseResult with valid=True and populated action on success,
        or valid=False with an error message on failure.
    """
    raw = text.strip()

    if not raw:
        return ParseResult(valid=False, error="empty input", raw=raw)

    m = _SCL_RE.match(raw)
    if not m:
        return ParseResult(
            valid=False,
            error=f"SCL syntax error: does not match '@anchor → relation [fields]' pattern. Got: {raw!r}",
            raw=raw,
        )

    anchor = m.group("anchor")
    relation = m.group("relation")
    fields_str = m.group("fields") or ""

    # Parse fields
    fields: dict[str, Any] = {}
    for fm in _FIELD_RE.finditer(fields_str):
        key = fm.group("key")
        val = _coerce_value(fm.group("value"))
        fields[key] = val

    action_dict = {"anchor": anchor, "relation": relation, "fields": fields, "raw": raw}

    # Validate against JSON schema
    try:
        jsonschema.validate(instance=action_dict, schema=_SCHEMA)
    except jsonschema.ValidationError as exc:
        return ParseResult(
            valid=False,
            error=f"SCL schema validation failed: {exc.message}",
            raw=raw,
        )

    action = SCLAction(anchor=anchor, relation=relation, fields=fields, raw=raw)
    return ParseResult(valid=True, action=action, raw=raw)


def emit(anchor: str, relation: str, **fields: Any) -> str:
    """
    Emit a canonical SCL string from components.

    Args:
        anchor: e.g. "@tool"
        relation: e.g. "call"
        **fields: key=value pairs

    Returns:
        Canonical SCL string.
    """
    if not fields:
        return f"{anchor} → {relation}"
    parts = []
    for k, v in fields.items():
        if isinstance(v, str):
            parts.append(f'{k}: "{v}"')
        elif isinstance(v, bool):
            parts.append(f"{k}: {str(v).lower()}")
        else:
            parts.append(f"{k}: {v}")
    return f"{anchor} → {relation} [{', '.join(parts)}]"
