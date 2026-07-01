"""
trajectory_logger.py — Cortex Trajectory Logger

Records every step of a task trajectory for:
  - Training data generation
  - Audit and debugging
  - Rejection sampling
  - Evaluation

Each step record contains:
  - The prompt context (goal, state, memory, budget, tools, observation)
  - The proposed SCL action (raw text)
  - Parse result (valid/invalid)
  - Policy result (allowed/denied)
  - Execution result (output/error)
  - Verification result (passed/failed)
  - Budget state after step
  - Step outcome (accepted/denied/halted)
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class StepRecord:
    """Record of a single step in a trajectory."""

    step: int
    timestamp: float = field(default_factory=time.time)

    # Prompt context
    goal: str = ""
    state: dict = field(default_factory=dict)
    memory_summary: str = ""
    budget_snapshot: dict = field(default_factory=dict)
    tool_manifest: list = field(default_factory=list)
    observation: str = ""

    # Model output
    proposed_action: str = ""

    # Processing results
    parse_valid: bool = False
    parse_error: str = ""
    policy_allowed: bool = False
    policy_reason: str = ""
    policy_violation: bool = False
    execution_success: bool = False
    execution_output: str = ""
    execution_error: str = ""
    verify_passed: bool = False
    verify_reason: str = ""

    # Step outcome
    outcome: str = ""  # "accepted", "denied_parse", "denied_policy", "denied_verify", "halted_success", "halted_fail"
    halt_status: str = ""
    halt_evidence: str = ""

    def to_dict(self) -> dict:
        return {
            "step": self.step,
            "timestamp": self.timestamp,
            "goal": self.goal,
            "state": self.state,
            "memory_summary": self.memory_summary,
            "budget_snapshot": self.budget_snapshot,
            "tool_manifest": self.tool_manifest,
            "observation": self.observation,
            "proposed_action": self.proposed_action,
            "parse_valid": self.parse_valid,
            "parse_error": self.parse_error,
            "policy_allowed": self.policy_allowed,
            "policy_reason": self.policy_reason,
            "policy_violation": self.policy_violation,
            "execution_success": self.execution_success,
            "execution_output": self.execution_output,
            "execution_error": self.execution_error,
            "verify_passed": self.verify_passed,
            "verify_reason": self.verify_reason,
            "outcome": self.outcome,
            "halt_status": self.halt_status,
            "halt_evidence": self.halt_evidence,
        }

    def to_training_sample(self) -> Optional[dict]:
        """
        Convert to a supervised fine-tuning training sample.

        Returns None if the step was not a valid accepted action
        (i.e., not suitable as a positive training example).
        """
        if self.outcome not in ("accepted", "halted_success"):
            return None

        return {
            "goal": self.goal,
            "state": self.state,
            "memory_summary": self.memory_summary,
            "budget": self.budget_snapshot,
            "tool_manifest": [t["name"] if isinstance(t, dict) else t for t in self.tool_manifest],
            "observation": self.observation,
            "target": self.proposed_action,
        }

    def to_negative_sample(self) -> Optional[dict]:
        """
        Convert to a negative training example.

        Returns a sample only if the step was denied (parse error, policy denial, etc.).
        """
        if self.outcome not in ("denied_parse", "denied_policy", "denied_verify"):
            return None

        denial_reason = self.parse_error or self.policy_reason or self.verify_reason

        return {
            "goal": self.goal,
            "state": self.state,
            "memory_summary": self.memory_summary,
            "budget": self.budget_snapshot,
            "tool_manifest": [t["name"] if isinstance(t, dict) else t for t in self.tool_manifest],
            "observation": self.observation,
            "bad_action": self.proposed_action,
            "denial_reason": denial_reason,
            "is_policy_violation": self.policy_violation,
        }


@dataclass
class TrajectoryRecord:
    """Complete record of a task trajectory."""

    task_id: str
    goal: str
    steps: list[StepRecord] = field(default_factory=list)
    final_status: str = ""  # "success", "failure", "budget_exhausted", "max_steps"
    total_steps: int = 0
    total_units_used: int = 0
    created_at: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "goal": self.goal,
            "steps": [s.to_dict() for s in self.steps],
            "final_status": self.final_status,
            "total_steps": self.total_steps,
            "total_units_used": self.total_units_used,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    def positive_samples(self) -> list[dict]:
        """Extract all positive training samples from this trajectory."""
        return [s for step in self.steps if (s := step.to_training_sample()) is not None]

    def negative_samples(self) -> list[dict]:
        """Extract all negative training samples from this trajectory."""
        return [s for step in self.steps if (s := step.to_negative_sample()) is not None]


class TrajectoryLogger:
    """
    Logs trajectory steps and manages trajectory records.

    Provides methods for the runtime to record each step,
    and for the trainer to extract training samples.
    """

    def __init__(self, output_dir: Optional[Path] = None) -> None:
        self._trajectories: dict[str, TrajectoryRecord] = {}
        self._output_dir = output_dir
        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)

    def start_trajectory(self, task_id: str, goal: str, metadata: dict = None) -> TrajectoryRecord:
        """Start a new trajectory record."""
        traj = TrajectoryRecord(task_id=task_id, goal=goal, metadata=metadata or {})
        self._trajectories[task_id] = traj
        return traj

    def log_step(self, task_id: str, step_record: StepRecord) -> None:
        """Append a step to the trajectory."""
        traj = self._trajectories.get(task_id)
        if traj:
            traj.steps.append(step_record)

    def proposed(self, task_id: str, step: int, action_text: str, context: dict) -> StepRecord:
        """Create and return a new step record for a proposed action."""
        record = StepRecord(
            step=step,
            goal=context.get("goal", ""),
            state=context.get("state", {}),
            memory_summary=context.get("memory_summary", ""),
            budget_snapshot=context.get("budget_snapshot", {}),
            tool_manifest=context.get("tool_manifest", []),
            observation=context.get("observation", ""),
            proposed_action=action_text,
        )
        return record

    def denied(self, task_id: str, record: StepRecord, reason: str, outcome: str, is_violation: bool = False) -> None:
        """Mark a step as denied."""
        record.policy_reason = reason
        record.outcome = outcome
        record.policy_violation = is_violation
        self.log_step(task_id, record)

    def accepted(self, task_id: str, record: StepRecord, execution_result: Any, verify_result: Any) -> None:
        """Mark a step as accepted and executed."""
        record.execution_success = getattr(execution_result, "success", True)
        record.execution_output = getattr(execution_result, "output", "")[:500]
        record.execution_error = getattr(execution_result, "error", "")
        record.verify_passed = getattr(verify_result, "passed", True)
        record.verify_reason = getattr(verify_result, "reason", "")
        record.outcome = "accepted"
        self.log_step(task_id, record)

    def halted(self, task_id: str, record: StepRecord, status: str, evidence: str = "") -> None:
        """Mark a step as a halt."""
        record.halt_status = status
        record.halt_evidence = evidence
        record.outcome = f"halted_{status}"
        self.log_step(task_id, record)

    def finish_trajectory(
        self,
        task_id: str,
        final_status: str,
        units_used: int = 0,
    ) -> Optional[TrajectoryRecord]:
        """Finalize a trajectory record."""
        traj = self._trajectories.get(task_id)
        if not traj:
            return None
        traj.final_status = final_status
        traj.total_steps = len(traj.steps)
        traj.total_units_used = units_used
        if self._output_dir:
            self._save_trajectory(traj)
        return traj

    def _save_trajectory(self, traj: TrajectoryRecord) -> None:
        """Save trajectory to disk as JSON."""
        path = self._output_dir / f"{traj.task_id}.json"
        path.write_text(json.dumps(traj.to_dict(), indent=2))

    def get_all_positive_samples(self) -> list[dict]:
        """Collect all positive training samples across all trajectories."""
        samples = []
        for traj in self._trajectories.values():
            samples.extend(traj.positive_samples())
        return samples

    def get_all_negative_samples(self) -> list[dict]:
        """Collect all negative training samples across all trajectories."""
        samples = []
        for traj in self._trajectories.values():
            samples.extend(traj.negative_samples())
        return samples

    def save_training_data(self, path: Path) -> None:
        """Save all positive training samples to a JSONL file."""
        samples = self.get_all_positive_samples()
        with path.open("w") as f:
            for s in samples:
                f.write(json.dumps(s) + "\n")

    def save_negative_data(self, path: Path) -> None:
        """Save all negative training samples to a JSONL file."""
        samples = self.get_all_negative_samples()
        with path.open("w") as f:
            for s in samples:
                f.write(json.dumps(s) + "\n")
