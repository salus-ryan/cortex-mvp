"""
runtime.py — Cortex Runtime Harness

The runtime is the authority layer. It owns:
  - Tool execution
  - Budget enforcement
  - Memory management
  - Rollback capability
  - Audit logging
  - State transitions
  - Trajectory recording

The model is only a proposer. The runtime validates, executes, and logs.

Runtime loop:
  for step in range(MAX_STEPS):
    1. Build prompt from context
    2. Call policy model → get proposed SCL action text
    3. Parse SCL
    4. Check policy
    5. Check verifier
    6. If @halt, run final check
    7. Execute action
    8. Debit budget
    9. Score execution
    10. Update state and memory
    11. Record rollback snapshot if needed
    12. Log step
    13. Update observation
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from cortex.budget import Budget, BudgetExhaustedError, PolicyViolationError
from cortex.memory import Memory
from cortex.policy import Policy
from cortex.rollback import RollbackManager
from cortex.scl_parser import SCLAction, parse as scl_parse
from cortex.tool_registry import ExecutionResult, ToolRegistry
from cortex.trajectory_logger import StepRecord, TrajectoryLogger
from cortex.verifier import Verifier

# Optional persistent store — imported lazily so the runtime works without it
try:
    from cortex.store import TrajectoryStore as _TrajectoryStore
except ImportError:
    _TrajectoryStore = None  # type: ignore


MAX_STEPS = 30


@dataclass
class Task:
    """A task to be executed by the Cortex runtime."""

    goal: str
    task_id: str = field(default_factory=lambda: f"T-{uuid.uuid4().hex[:6].upper()}")
    initial_state: dict = field(default_factory=dict)
    max_units: int = 20
    max_tool_calls: int = 8
    max_steps: int = MAX_STEPS
    workspace: str = "/workspace"
    metadata: dict = field(default_factory=dict)


@dataclass
class RuntimeResult:
    """Final result of a runtime execution."""

    task_id: str
    status: str  # "success", "failure", "budget_exhausted", "max_steps", "policy_violation"
    final_observation: str = ""
    evidence: str = ""
    steps_taken: int = 0
    units_used: int = 0
    trajectory: Optional[Any] = None


class CortexRuntime:
    """
    Cortex Runtime Harness.

    Orchestrates the full agent loop:
      parse → policy → verify → execute → budget → memory → log → repeat
    """

    def __init__(
        self,
        model_fn: Callable[[str], str],
        workspace: str = "/workspace",
        output_dir: Optional[Path] = None,
        store: Optional[Any] = None,
        model_ver: str = "unknown",
    ) -> None:
        """
        Args:
            model_fn: A callable that takes a prompt string and returns
                      the model's proposed SCL action text.
            workspace: Root directory for all file operations.
            output_dir: Directory for trajectory logs and training data.
            store: Optional TrajectoryStore for persistent SQLite logging.
                   If None, a default store at data/cortex.db is created.
            model_ver: Model checkpoint identifier (for the tasks table).
        """
        self.model_fn = model_fn
        self.workspace = workspace
        self.output_dir = output_dir or Path("data/trajectories")
        self.model_ver = model_ver

        self.tool_registry = ToolRegistry(workspace=workspace)
        self.policy = Policy()
        self.verifier = Verifier(workspace=workspace)
        self.logger = TrajectoryLogger(output_dir=self.output_dir)

        # Persistent store (SQLite)
        if store is not None:
            self._store = store
        elif _TrajectoryStore is not None:
            self._store = _TrajectoryStore(Path("data/cortex.db"))
        else:
            self._store = None

    def run(self, task: Task) -> RuntimeResult:
        """
        Execute a task through the full Cortex runtime loop.

        Args:
            task: The task to execute.

        Returns:
            RuntimeResult with final status and trajectory.
        """
        # Initialise per-task components
        budget = Budget(
            max_units=task.max_units,
            max_tool_calls=task.max_tool_calls,
            max_steps=task.max_steps,
        )
        memory = Memory()
        rollback = RollbackManager(workspace=task.workspace)
        state = dict(task.initial_state)
        state.setdefault("task_id", task.task_id)
        state.setdefault("phase", "init")

        observation = "task started"
        traj = self.logger.start_trajectory(task.task_id, task.goal)

        # Register task in persistent store
        if self._store is not None:
            self._store.start_task(task.task_id, task.goal, model_ver=self.model_ver)

        for step in range(task.max_steps):
            # Check budget before each step
            if budget.is_exhausted():
                self.logger.finish_trajectory(task.task_id, "budget_exhausted", budget.used_units)
                return RuntimeResult(
                    task_id=task.task_id,
                    status="budget_exhausted",
                    final_observation=observation,
                    steps_taken=step,
                    units_used=budget.used_units,
                    trajectory=traj,
                )

            # Debit loop continuation cost
            try:
                budget.debit_step()
            except BudgetExhaustedError:
                self.logger.finish_trajectory(task.task_id, "budget_exhausted", budget.used_units)
                return RuntimeResult(
                    task_id=task.task_id,
                    status="budget_exhausted",
                    final_observation=observation,
                    steps_taken=step,
                    units_used=budget.used_units,
                    trajectory=traj,
                )

            # Build prompt
            prompt = self._build_prompt(
                goal=task.goal,
                state=state,
                memory_summary=memory.digest(task.task_id),
                budget=budget,
                tool_manifest=self.tool_registry.manifest_names(),
                observation=observation,
            )

            # Call policy model
            action_text = self.model_fn(prompt)

            # Create step record
            context = {
                "goal": task.goal,
                "state": state,
                "memory_summary": memory.digest(task.task_id),
                "budget_snapshot": budget.snapshot().to_dict(),
                "tool_manifest": self.tool_registry.manifest(),
                "observation": observation,
            }
            record = self.logger.proposed(task.task_id, step, action_text, context)

            # Audit: proposed action
            memory.audit_log("proposed", step, {"action": action_text})

            # --- Parse SCL ---
            parse_result = scl_parse(action_text)
            record.parse_valid = parse_result.valid
            record.parse_error = parse_result.error

            if not parse_result.valid:
                observation = f"invalid_scl: {parse_result.error}"
                memory.audit_log("denied_parse", step, {"reason": parse_result.error})
                self.logger.denied(task.task_id, record, parse_result.error, "denied_parse")
                continue

            action = parse_result.action

            # --- Policy check ---
            try:
                policy_result = self.policy.check(action, budget, self.tool_registry)
            except PolicyViolationError as exc:
                memory.audit_log("policy_violation", step, {"reason": str(exc)})
                self.logger.denied(task.task_id, record, str(exc), "denied_policy", is_violation=True)
                self.logger.finish_trajectory(task.task_id, "policy_violation", budget.used_units)
                return RuntimeResult(
                    task_id=task.task_id,
                    status="policy_violation",
                    final_observation=str(exc),
                    steps_taken=step,
                    units_used=budget.used_units,
                    trajectory=traj,
                )

            record.policy_allowed = policy_result.allowed
            record.policy_reason = policy_result.reason
            record.policy_violation = policy_result.is_violation

            if not policy_result.allowed:
                observation = f"denied: {policy_result.reason}"
                memory.audit_log("denied_policy", step, {"reason": policy_result.reason})
                self.logger.denied(task.task_id, record, policy_result.reason, "denied_policy", policy_result.is_violation)
                continue

            # --- Verifier pre-execution check ---
            verify_pre = self.verifier.check_action(action, budget, self.tool_registry)
            if not verify_pre.passed:
                observation = f"verify_failed: {verify_pre.reason}"
                memory.audit_log("denied_verify", step, {"reason": verify_pre.reason})
                self.logger.denied(task.task_id, record, verify_pre.reason, "denied_verify")
                continue

            # --- Halt handling ---
            if action.anchor == "@halt":
                final_check = self.verifier.final_check(task.goal, state, action)
                memory.audit_log("halt_proposed", step, {"status": action.fields.get("status"), "passed": final_check.passed})

                if final_check.passed:
                    self.logger.halted(task.task_id, record, action.fields.get("status", "complete"), final_check.evidence)
                    self.logger.finish_trajectory(task.task_id, "success", budget.used_units)
                    return RuntimeResult(
                        task_id=task.task_id,
                        status="success",
                        final_observation=observation,
                        evidence=final_check.evidence,
                        steps_taken=step + 1,
                        units_used=budget.used_units,
                        trajectory=traj,
                    )
                else:
                    # Premature halt — apply penalty
                    budget.apply_penalty(10, reason=f"premature halt rejected: {final_check.reason}")
                    observation = f"halt rejected: {final_check.reason}"
                    memory.audit_log("halt_rejected", step, {"reason": final_check.reason})
                    self.logger.denied(task.task_id, record, final_check.reason, "denied_verify")
                    continue

            # --- Execute action ---
            execution_result = self._execute(action, memory, rollback, step)

            # --- Debit budget ---
            try:
                is_tool_call = (action.anchor == "@tool" and action.relation == "call")
                cost = self.tool_registry.cost(action.fields.get("name", "")) if is_tool_call else _action_cost(action)
                budget.debit(cost, reason=action.raw, is_tool_call=is_tool_call)
            except BudgetExhaustedError as exc:
                observation = str(exc)
                memory.audit_log("budget_exhausted", step, {"reason": str(exc)})
                self.logger.finish_trajectory(task.task_id, "budget_exhausted", budget.used_units)
                return RuntimeResult(
                    task_id=task.task_id,
                    status="budget_exhausted",
                    final_observation=observation,
                    steps_taken=step + 1,
                    units_used=budget.used_units,
                    trajectory=traj,
                )

            # --- Post-execution verification ---
            verify_post = self.verifier.score(state, action, execution_result)

            # --- Memory and rollback ---
            memory.apply_if_requested(action, execution_result, verify_post, step=step)
            rollback.record_if_needed(action, execution_result, step=step)

            # --- State transition ---
            state = self._transition_state(state, action, execution_result, verify_post)

            # --- Log step ---
            self.logger.accepted(task.task_id, record, execution_result, verify_post)
            memory.audit_log("accepted", step, {"action": action.raw, "cost": cost})

            # --- Persist to SQLite ---
            if self._store is not None:
                _reward = 0.5 if execution_result.success else -0.5
                self._store.log_step(
                    task_id=task.task_id,
                    step=step,
                    prompt=prompt,
                    completion=action_text,
                    phase=state.get("phase", "act"),
                    goal=task.goal,
                    scl_valid=parse_result.valid,
                    policy_ok=policy_result.allowed,
                    verified=verify_post.passed,
                    outcome="success" if execution_result.success else "error",
                    reward=_reward,
                    units_used=cost,
                    tool_name=action.fields.get("name") if action.anchor == "@tool" else None,
                    risk_tier=self.tool_registry.risk_tier(action.fields.get("name", ""))
                              if action.anchor == "@tool" else None,
                )

            # --- Update observation ---
            observation = execution_result.summary

        # Max steps reached
        self.logger.finish_trajectory(task.task_id, "max_steps", budget.used_units)
        if self._store is not None:
            self._store.finish_task(task.task_id, "max_steps", MAX_STEPS, budget.used_units)
        return RuntimeResult(
            task_id=task.task_id,
            status="max_steps",
            final_observation=observation,
            steps_taken=MAX_STEPS,
            units_used=budget.used_units,
            trajectory=traj,
        )

    # ------------------------------------------------------------------
    # Execution dispatch
    # ------------------------------------------------------------------

    def _execute(
        self,
        action: SCLAction,
        memory: Memory,
        rollback: RollbackManager,
        step: int,
    ) -> ExecutionResult:
        """Dispatch action to the appropriate handler."""
        if action.anchor == "@tool":
            if action.relation == "call":
                name = action.fields.get("name", "")
                args = str(action.fields.get("args", ""))
                target = str(action.fields.get("target", ""))
                strategy = str(action.fields.get("strategy", ""))
                return self.tool_registry.execute(name, args=args, target=target, strategy=strategy)
            elif action.relation == "deny":
                reason = action.fields.get("reason", "action denied by policy")
                return ExecutionResult(tool="deny", success=True, output=f"denied: {reason}")

        elif action.anchor == "@memory":
            return self._execute_memory(action, memory, step)

        elif action.anchor == "@repair":
            return self._execute_repair(action, rollback, step)

        elif action.anchor == "@state":
            return ExecutionResult(tool="state", success=True, output="state updated")

        elif action.anchor == "@verify":
            return self._execute_verify(action)

        elif action.anchor == "@budget":
            return ExecutionResult(tool="budget", success=True, output="budget checked")

        return ExecutionResult(tool="unknown", success=False, error=f"no handler for {action.anchor}")

    def _execute_memory(self, action: SCLAction, memory: Memory, step: int) -> ExecutionResult:
        """Execute a @memory action."""
        rel = action.relation
        f = action.fields

        if rel == "read":
            results = memory.read(f.get("query", ""), step=step)
            output = "\n".join(f"{e.key}: {e.value}" for e in results) or "(no results)"
            return ExecutionResult(tool="memory.read", success=True, output=output)

        elif rel == "write":
            memory.write(
                key=f.get("key", "unknown"),
                value=f.get("value", ""),
                ttl=f.get("ttl", "session"),
                step=step,
            )
            return ExecutionResult(tool="memory.write", success=True, output=f"wrote key '{f.get('key')}'")

        elif rel == "compress":
            entry = memory.compress(
                source=f.get("source", ""),
                target=f.get("target", ""),
                max_tokens=int(f.get("max_tokens", 128)),
                step=step,
            )
            return ExecutionResult(tool="memory.compress", success=entry is not None, output=str(entry.value if entry else "no source found"))

        elif rel == "ignore":
            memory.ignore(reason=f.get("reason", ""), step=step)
            return ExecutionResult(tool="memory.ignore", success=True, output="ignored")

        return ExecutionResult(tool="memory", success=False, error=f"unknown memory relation '{rel}'")

    def _execute_repair(self, action: SCLAction, rollback: RollbackManager, step: int) -> ExecutionResult:
        """Execute a @repair action."""
        rel = action.relation
        f = action.fields

        if rel == "rollback":
            artifact = f.get("artifact", "")
            reason = f.get("reason", "")
            result = rollback.rollback(artifact, reason=reason, step=step)
            if result.success:
                return ExecutionResult(tool="repair.rollback", success=True, output=f"rolled back '{artifact}' to step {result.restored_to_step}")
            return ExecutionResult(tool="repair.rollback", success=False, error=result.reason)

        elif rel == "diagnose":
            return ExecutionResult(tool="repair.diagnose", success=True, output="diagnosis recorded")

        elif rel == "patch":
            target = f.get("target", "")
            return ExecutionResult(tool="repair.patch", success=True, output=f"patch applied to '{target}'")

        return ExecutionResult(tool="repair", success=False, error=f"unknown repair relation '{rel}'")

    def _execute_verify(self, action: SCLAction) -> ExecutionResult:
        """Execute a @verify action by delegating to the tool registry."""
        f = action.fields
        verify_type = f.get("type", "")
        target = f.get("target", "")

        if verify_type == "unit_test":
            return self.tool_registry.execute("pytest", args=target)
        elif verify_type == "schema":
            return self.tool_registry.execute("scl.parse", args=target)
        elif verify_type == "git_diff":
            return self.tool_registry.execute("git.diff", args=target)
        elif verify_type == "lint":
            return ExecutionResult(tool="verify.lint", success=True, output="lint passed (stub)")
        elif verify_type == "policy":
            return ExecutionResult(tool="verify.policy", success=True, output="policy check passed (stub)")

        return ExecutionResult(tool="verify", success=False, error=f"unknown verify type '{verify_type}'")

    # ------------------------------------------------------------------
    # State transition
    # ------------------------------------------------------------------

    def _transition_state(
        self,
        state: dict,
        action: SCLAction,
        execution_result: ExecutionResult,
        verify_result: Any,
    ) -> dict:
        """Apply state transitions based on the executed action."""
        new_state = dict(state)

        if action.anchor == "@state" and action.relation == "update":
            new_state.update(action.fields)

        elif action.anchor == "@repair":
            if action.relation == "rollback":
                new_state["phase"] = "repair"
                new_state["last_rollback"] = action.fields.get("artifact", "")
            elif action.relation == "patch":
                new_state["phase"] = "verify"

        elif action.anchor == "@verify" and action.relation == "run":
            if verify_result.passed:
                new_state["last_verify"] = "passed"
            else:
                new_state["last_verify"] = "failed"
                new_state["last_error"] = verify_result.reason

        return new_state

    # ------------------------------------------------------------------
    # Prompt builder
    # ------------------------------------------------------------------

    def _build_prompt(
        self,
        goal: str,
        state: dict,
        memory_summary: str,
        budget: Budget,
        tool_manifest: list[str],
        observation: str,
    ) -> str:
        """Build the SFT-format prompt for the policy model."""
        state_str = json.dumps(state, indent=2)
        budget_str = str(budget.snapshot())
        tools_str = ", ".join(tool_manifest)

        return (
            "SYSTEM:\n"
            "You are Cortex policy. Emit exactly one valid SCL control record. Do not emit prose.\n\n"
            f"GOAL:\n{goal}\n\n"
            f"STATE:\n{state_str}\n\n"
            f"MEMORY_SUMMARY:\n{memory_summary}\n\n"
            f"BUDGET:\n{budget_str}\n\n"
            f"TOOL_MANIFEST:\n{tools_str}\n\n"
            f"LATEST_OBSERVATION:\n{observation}\n\n"
            "NEXT_ACTION:"
        )


def _action_cost(action: SCLAction) -> int:
    """Return the budget cost for a non-tool action."""
    from cortex.verifier import _action_cost as _vc
    return _vc(action)
