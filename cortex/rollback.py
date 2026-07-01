"""
rollback.py — Cortex Rollback and Self-Repair System

Manages artifact snapshots and rollback operations.
Self-repair must not mean unrestricted self-modification.

Repair sequence:
  1. detect failed action
  2. diagnose likely cause
  3. rollback if necessary
  4. apply minimal patch if allowed
  5. verify again
  6. write lesson to memory
  7. halt only after evidence

Rollback is confined to:
  - File patches applied within the workspace
  - State transitions recorded in the trajectory
  - Memory writes with session/ephemeral TTL

Rollback cannot:
  - Modify the runtime harness
  - Undo audit log entries
  - Restore persistent semantic memory rules
"""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class Snapshot:
    """A point-in-time snapshot of a mutable artifact."""

    artifact_id: str
    artifact_type: str  # "file", "state", "memory_entry"
    content: Any
    step: int
    timestamp: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type,
            "content": self.content if not isinstance(self.content, bytes) else "<binary>",
            "step": self.step,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }


@dataclass
class RollbackResult:
    """Result of a rollback operation."""

    success: bool
    artifact_id: str
    reason: str = ""
    restored_to_step: int = -1


class RollbackManager:
    """
    Manages snapshots and rollback for Cortex.

    Snapshots are taken before any write operation.
    Rollback restores the most recent snapshot for a given artifact.
    """

    def __init__(self, workspace: str = "/workspace") -> None:
        self.workspace = workspace
        self._snapshots: dict[str, list[Snapshot]] = {}  # artifact_id → [snapshots]
        self._rollback_log: list[dict] = []

    # ------------------------------------------------------------------
    # Snapshot management
    # ------------------------------------------------------------------

    def snapshot_file(self, path: str, step: int) -> Optional[Snapshot]:
        """
        Take a snapshot of a file before modification.

        Args:
            path: Relative or absolute path to the file.
            step: Current trajectory step number.

        Returns:
            Snapshot if file exists, None otherwise.
        """
        file_path = self._resolve_path(path)
        if not file_path.exists():
            return None

        content = file_path.read_text(encoding="utf-8", errors="replace")
        snap = Snapshot(
            artifact_id=str(file_path),
            artifact_type="file",
            content=content,
            step=step,
            metadata={"path": str(file_path)},
        )
        self._snapshots.setdefault(str(file_path), []).append(snap)
        return snap

    def snapshot_state(self, state: dict, step: int, artifact_id: str = "task_state") -> Snapshot:
        """Take a snapshot of the current task state dict."""
        snap = Snapshot(
            artifact_id=artifact_id,
            artifact_type="state",
            content=copy.deepcopy(state),
            step=step,
        )
        self._snapshots.setdefault(artifact_id, []).append(snap)
        return snap

    def record_if_needed(
        self,
        action: Any,
        execution_result: Any,
        step: int = 0,
    ) -> None:
        """
        Record a snapshot if the action is a write operation.

        Called by the runtime after each action to maintain rollback capability.
        """
        if not hasattr(action, "anchor"):
            return

        # Record file snapshots before patch operations
        if action.anchor == "@tool" and action.relation == "call":
            name = action.fields.get("name", "")
            if name == "shell.patch":
                target = action.fields.get("target", "")
                if target:
                    self.snapshot_file(target, step)

        # Record repair patches
        if action.anchor == "@repair" and action.relation == "patch":
            target = action.fields.get("target", "")
            if target:
                self.snapshot_file(target, step)

    # ------------------------------------------------------------------
    # Rollback
    # ------------------------------------------------------------------

    def rollback(self, artifact_id: str, reason: str = "", step: int = 0) -> RollbackResult:
        """
        Roll back an artifact to its most recent snapshot.

        Args:
            artifact_id: The artifact to roll back (file path or state ID).
            reason: Human-readable reason for rollback.
            step: Current step number.

        Returns:
            RollbackResult indicating success or failure.
        """
        snaps = self._snapshots.get(artifact_id, [])
        if not snaps:
            return RollbackResult(
                success=False,
                artifact_id=artifact_id,
                reason=f"no snapshot found for artifact '{artifact_id}'",
            )

        # Restore most recent snapshot
        snap = snaps[-1]

        if snap.artifact_type == "file":
            try:
                file_path = Path(snap.metadata["path"])
                file_path.write_text(snap.content, encoding="utf-8")
                self._rollback_log.append({
                    "step": step,
                    "artifact_id": artifact_id,
                    "reason": reason,
                    "restored_to_step": snap.step,
                    "timestamp": time.time(),
                })
                return RollbackResult(
                    success=True,
                    artifact_id=artifact_id,
                    reason=reason,
                    restored_to_step=snap.step,
                )
            except Exception as exc:
                return RollbackResult(
                    success=False,
                    artifact_id=artifact_id,
                    reason=f"rollback failed: {exc}",
                )

        elif snap.artifact_type == "state":
            # State rollback returns the snapshot content for the runtime to apply
            self._rollback_log.append({
                "step": step,
                "artifact_id": artifact_id,
                "reason": reason,
                "restored_to_step": snap.step,
                "timestamp": time.time(),
            })
            return RollbackResult(
                success=True,
                artifact_id=artifact_id,
                reason=reason,
                restored_to_step=snap.step,
            )

        return RollbackResult(
            success=False,
            artifact_id=artifact_id,
            reason=f"unknown artifact type '{snap.artifact_type}'",
        )

    def get_snapshot(self, artifact_id: str) -> Optional[Snapshot]:
        """Return the most recent snapshot for an artifact."""
        snaps = self._snapshots.get(artifact_id, [])
        return snaps[-1] if snaps else None

    def get_rollback_log(self) -> list[dict]:
        return list(self._rollback_log)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_path(self, path: str) -> Path:
        p = Path(path)
        if p.is_absolute():
            return p
        return Path(self.workspace) / p
