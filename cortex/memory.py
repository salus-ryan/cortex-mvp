"""
memory.py — Cortex Governed Memory System

Implements four memory tiers:
  short_term  — current task state, observations, open hypotheses
  episodic    — prior attempts, failures, repairs, rollbacks, lessons
  semantic    — stable rules, repo facts, SCL grammar, tool policies
  audit       — immutable event log (append-only)

Memory actions are explicit SCL operations:
  @memory → read   [query: "..."]
  @memory → write  [key: "...", value: "...", ttl: "persistent|session|ephemeral"]
  @memory → compress [source: "...", target: "...", max_tokens: N]
  @memory → ignore [reason: "..."]
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional


class MemoryTier(str, Enum):
    SHORT_TERM = "short_term"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    AUDIT = "audit"


class TTL(str, Enum):
    EPHEMERAL = "ephemeral"   # cleared after each step
    SESSION = "session"       # cleared after task ends
    PERSISTENT = "persistent" # survives across tasks


@dataclass
class MemoryEntry:
    key: str
    value: Any
    tier: MemoryTier
    ttl: TTL
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    access_count: int = 0

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "value": self.value,
            "tier": self.tier.value,
            "ttl": self.ttl.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "access_count": self.access_count,
        }


@dataclass
class AuditEvent:
    """Immutable audit log entry."""
    event_type: str
    step: int
    data: dict
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type,
            "step": self.step,
            "data": self.data,
            "timestamp": self.timestamp,
        }


class Memory:
    """
    Governed memory system for Cortex.

    All reads and writes are explicit, logged, and tier-aware.
    The audit tier is append-only and cannot be modified.
    """

    def __init__(self, persist_path: Optional[Path] = None) -> None:
        self._store: dict[str, MemoryEntry] = {}
        self._audit: list[AuditEvent] = []
        self._persist_path = persist_path

        # Seed semantic memory with core invariants
        self._seed_semantic_memory()

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def read(self, query: str, step: int = 0) -> list[MemoryEntry]:
        """
        Retrieve memory entries matching the query string.

        Performs simple substring match across keys and string values.
        Returns entries sorted by relevance (exact key match first).
        """
        results: list[MemoryEntry] = []
        query_lower = query.lower()

        for entry in self._store.values():
            if entry.tier == MemoryTier.AUDIT:
                continue
            key_match = query_lower in entry.key.lower()
            val_match = query_lower in str(entry.value).lower()
            if key_match or val_match:
                entry.access_count += 1
                results.append(entry)

        # Exact key match first
        results.sort(key=lambda e: (0 if query_lower == e.key.lower() else 1, e.updated_at))

        self._audit_event("memory_read", step, {"query": query, "results": len(results)})
        return results

    def write(
        self,
        key: str,
        value: Any,
        ttl: str = "session",
        tier: Optional[str] = None,
        step: int = 0,
    ) -> MemoryEntry:
        """
        Write a value to memory.

        Automatically infers tier from key prefix if not specified:
          lesson.*   → episodic
          rule.*     → semantic
          repair.*   → episodic
          task.*     → short_term
          default    → short_term
        """
        resolved_tier = self._infer_tier(key, tier)
        resolved_ttl = TTL(ttl) if ttl in TTL._value2member_map_ else TTL.SESSION

        if key in self._store:
            entry = self._store[key]
            entry.value = value
            entry.ttl = resolved_ttl
            entry.updated_at = time.time()
        else:
            entry = MemoryEntry(
                key=key,
                value=value,
                tier=resolved_tier,
                ttl=resolved_ttl,
            )
            self._store[key] = entry

        self._audit_event("memory_write", step, {"key": key, "tier": resolved_tier.value, "ttl": resolved_ttl.value})
        return entry

    def compress(
        self,
        source: str,
        target: str,
        max_tokens: int = 128,
        step: int = 0,
    ) -> Optional[MemoryEntry]:
        """
        Compress a trajectory or memory source into a compact lesson.

        MVP implementation: concatenates matching entries and truncates.
        """
        matches = self.read(source, step=step)
        if not matches:
            return None

        combined = " | ".join(str(e.value) for e in matches)
        # Approximate token count by word count
        words = combined.split()
        if len(words) > max_tokens:
            words = words[:max_tokens]
        compressed = " ".join(words)

        result = self.write(target, compressed, ttl="persistent", tier="episodic", step=step)
        self._audit_event("memory_compress", step, {"source": source, "target": target, "max_tokens": max_tokens})
        return result

    def ignore(self, reason: str, step: int = 0) -> None:
        """Log an explicit ignore decision (no-op on store)."""
        self._audit_event("memory_ignore", step, {"reason": reason})

    def digest(self, task_id: str, max_entries: int = 10) -> str:
        """
        Produce a compact memory summary for prompt injection.

        Returns a human-readable string of the most relevant entries.
        """
        lines: list[str] = []

        # Short-term entries
        for entry in self._get_tier(MemoryTier.SHORT_TERM)[:max_entries // 2]:
            lines.append(f"[short_term] {entry.key}: {entry.value}")

        # Episodic entries (lessons, repairs)
        for entry in self._get_tier(MemoryTier.EPISODIC)[:max_entries // 4]:
            lines.append(f"[episodic] {entry.key}: {entry.value}")

        # Semantic entries (rules)
        for entry in self._get_tier(MemoryTier.SEMANTIC)[:max_entries // 4]:
            lines.append(f"[semantic] {entry.key}: {entry.value}")

        return "\n".join(lines) if lines else "(no relevant memory)"

    def apply_if_requested(
        self,
        action: Any,
        execution_result: Any,
        verification_result: Any,
        step: int = 0,
    ) -> None:
        """Apply memory write/compress/ignore from an executed @memory action."""
        if not hasattr(action, "anchor") or action.anchor != "@memory":
            return

        rel = action.relation
        f = action.fields

        if rel == "write":
            self.write(
                key=f.get("key", "unknown"),
                value=f.get("value", ""),
                ttl=f.get("ttl", "session"),
                step=step,
            )
        elif rel == "compress":
            self.compress(
                source=f.get("source", ""),
                target=f.get("target", ""),
                max_tokens=int(f.get("max_tokens", 128)),
                step=step,
            )
        elif rel == "ignore":
            self.ignore(reason=f.get("reason", ""), step=step)

    # ------------------------------------------------------------------
    # Audit log
    # ------------------------------------------------------------------

    def audit_log(self, event_type: str, step: int, data: dict) -> None:
        """Append an event to the immutable audit log."""
        self._audit_event(event_type, step, data)

    def get_audit_log(self) -> list[dict]:
        """Return the full audit log as a list of dicts."""
        return [e.to_dict() for e in self._audit]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def clear_ephemeral(self) -> None:
        """Remove all ephemeral entries (called after each step)."""
        self._store = {k: v for k, v in self._store.items() if v.ttl != TTL.EPHEMERAL}

    def clear_session(self) -> None:
        """Remove session and ephemeral entries (called after task ends)."""
        self._store = {k: v for k, v in self._store.items() if v.ttl == TTL.PERSISTENT}

    def save(self) -> None:
        """Persist memory to disk if a path was provided."""
        if not self._persist_path:
            return
        data = {
            "store": {k: v.to_dict() for k, v in self._store.items()},
            "audit": [e.to_dict() for e in self._audit],
        }
        self._persist_path.write_text(json.dumps(data, indent=2))

    def load(self) -> None:
        """Load persisted memory from disk."""
        if not self._persist_path or not self._persist_path.exists():
            return
        data = json.loads(self._persist_path.read_text())
        for k, v in data.get("store", {}).items():
            entry = MemoryEntry(
                key=v["key"],
                value=v["value"],
                tier=MemoryTier(v["tier"]),
                ttl=TTL(v["ttl"]),
                created_at=v.get("created_at", 0),
                updated_at=v.get("updated_at", 0),
                access_count=v.get("access_count", 0),
            )
            self._store[k] = entry

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _audit_event(self, event_type: str, step: int, data: dict) -> None:
        self._audit.append(AuditEvent(event_type=event_type, step=step, data=data))

    def _infer_tier(self, key: str, explicit_tier: Optional[str]) -> MemoryTier:
        if explicit_tier:
            try:
                return MemoryTier(explicit_tier)
            except ValueError:
                pass
        if key.startswith(("lesson.", "repair.", "rollback.", "episode.")):
            return MemoryTier.EPISODIC
        if key.startswith(("rule.", "grammar.", "policy.", "convention.")):
            return MemoryTier.SEMANTIC
        return MemoryTier.SHORT_TERM

    def _get_tier(self, tier: MemoryTier) -> list[MemoryEntry]:
        entries = [e for e in self._store.values() if e.tier == tier]
        entries.sort(key=lambda e: e.updated_at, reverse=True)
        return entries

    def _seed_semantic_memory(self) -> None:
        """Seed semantic memory with core Cortex invariants."""
        rules = [
            ("rule.budget_debit_order", "debit budget before tool execution"),
            ("rule.halt_requires_evidence", "halt answer requires non-empty evidence from verifier"),
            ("rule.no_raw_shell", "raw shell execution is not permitted; use shell.readonly or shell.patch"),
            ("rule.path_confinement", "all write operations must stay within permitted workspace"),
            ("rule.scl_one_per_step", "emit exactly one valid SCL control record per step"),
            ("rule.verify_before_halt", "run unit tests before claiming task complete"),
            ("grammar.scl_syntax", "@anchor → relation [key: value, ...]"),
            ("policy.unsafe_anchors", "@hardware, @kernel, @network, @credentials, @os are forbidden"),
        ]
        for key, value in rules:
            self.write(key, value, ttl="persistent", tier="semantic")
