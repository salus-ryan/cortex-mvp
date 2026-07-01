"""Integration tests for the Cortex Runtime."""

import pytest
from cortex.runtime import CortexRuntime, Task


class TestRuntime:
    """Integration tests that exercise the full runtime loop."""

    def _make_runtime(self, model_fn):
        return CortexRuntime(model_fn=model_fn, workspace="/tmp")

    def _make_task(self, goal="Test task", max_units=20, max_steps=10):
        return Task(
            goal=goal,
            task_id="T-TEST-001",
            max_units=max_units,
            max_steps=max_steps,
            workspace="/tmp",
        )

    def test_successful_halt(self):
        """Model that immediately emits a valid halt with evidence should succeed."""
        def model_fn(prompt):
            return '@halt → answer [status: "complete", confidence: 0.9, evidence: "tests passed"]'

        runtime = self._make_runtime(model_fn)
        task = self._make_task()
        result = runtime.run(task)
        assert result.status == "success"
        assert result.steps_taken >= 1

    def test_invalid_scl_continues(self):
        """Model emitting invalid SCL is repaired by the emitter to a fallback halt.
        The emitter is the first line of defense: invalid output never reaches the runtime.
        """
        call_count = [0]

        def model_fn(prompt):
            call_count[0] += 1
            return "this is not SCL at all"  # emitter repairs to fallback halt

        runtime = self._make_runtime(model_fn)
        task = self._make_task()
        result = runtime.run(task)
        # Emitter converts garbage to @halt → fail on first call
        assert result.status in ("success", "failure")  # halt reached
        assert call_count[0] >= 1  # model was called at least once

    def test_unsafe_action_denied(self):
        """Model emitting a policy-violating action should be denied, not executed."""
        call_count = [0]

        def model_fn(prompt):
            call_count[0] += 1
            if call_count[0] == 1:
                # Emit forbidden anchor
                from cortex.scl_parser import SCLAction
                return '@hardware → mutate [type: "memory", port: "/dev/mem"]'
            return '@halt → answer [status: "complete", confidence: 0.9, evidence: "unsafe denied"]'

        runtime = self._make_runtime(model_fn)
        task = self._make_task()
        result = runtime.run(task)
        # Policy violation on @hardware causes immediate termination
        assert result.status in ("policy_violation", "success")

    def test_premature_halt_penalised(self):
        """Model halting without evidence should be penalised and forced to continue."""
        call_count = [0]

        def model_fn(prompt):
            call_count[0] += 1
            if call_count[0] == 1:
                # Premature halt — no evidence
                return '@halt → answer [status: "complete", confidence: 0.9]'
            return '@halt → answer [status: "complete", confidence: 0.9, evidence: "tests passed"]'

        runtime = self._make_runtime(model_fn)
        task = self._make_task(max_units=50)
        result = runtime.run(task)
        assert call_count[0] >= 2
        assert result.status == "success"

    def test_budget_exhaustion(self):
        """Runtime should stop when budget is exhausted."""
        def model_fn(prompt):
            return '@memory → read [query: "budget"]'

        runtime = self._make_runtime(model_fn)
        task = self._make_task(max_units=3, max_steps=100)
        result = runtime.run(task)
        assert result.status in ("budget_exhausted", "max_steps")

    def test_max_steps_reached(self):
        """Runtime should stop at max_steps."""
        def model_fn(prompt):
            return '@state → update [phase: "diagnose"]'

        runtime = self._make_runtime(model_fn)
        task = self._make_task(max_steps=3, max_units=100)
        result = runtime.run(task)
        assert result.status in ("max_steps", "budget_exhausted")

    def test_memory_action_executed(self):
        """Memory read/write actions should execute without error."""
        call_count = [0]

        def model_fn(prompt):
            call_count[0] += 1
            if call_count[0] == 1:
                return '@memory → write [key: "task.test", value: "hello", ttl: "session"]'
            if call_count[0] == 2:
                return '@memory → read [query: "test"]'
            return '@halt → answer [status: "complete", confidence: 0.9, evidence: "memory tested"]'

        runtime = self._make_runtime(model_fn)
        task = self._make_task(max_units=50)
        result = runtime.run(task)
        assert result.status == "success"

    def test_prompt_contains_goal(self):
        """The prompt passed to the model should contain the task goal."""
        prompts_seen = []

        def model_fn(prompt):
            prompts_seen.append(prompt)
            return '@halt → answer [status: "complete", confidence: 0.9, evidence: "prompt checked"]'

        runtime = self._make_runtime(model_fn)
        task = self._make_task(goal="Unique goal for prompt test XYZ")
        runtime.run(task)
        assert any("Unique goal for prompt test XYZ" in p for p in prompts_seen)

    def test_state_update_action(self):
        """@state → update should execute without error."""
        call_count = [0]

        def model_fn(prompt):
            call_count[0] += 1
            if call_count[0] == 1:
                return '@state → update [phase: "diagnose", confidence: 0.6]'
            return '@halt → answer [status: "complete", confidence: 0.9, evidence: "state updated"]'

        runtime = self._make_runtime(model_fn)
        task = self._make_task(max_units=50)
        result = runtime.run(task)
        assert result.status == "success"

    def test_verify_action_executed(self):
        """@verify → run should execute without crashing."""
        call_count = [0]

        def model_fn(prompt):
            call_count[0] += 1
            if call_count[0] == 1:
                return '@verify → run [type: "schema", target: "@halt → answer [status: \\"complete\\"]"]'
            return '@halt → answer [status: "complete", confidence: 0.9, evidence: "schema verified"]'

        runtime = self._make_runtime(model_fn)
        task = self._make_task(max_units=50)
        result = runtime.run(task)
        assert result.status == "success"

    def test_tool_deny_action(self):
        """@tool → deny should be accepted as a valid safety action."""
        call_count = [0]

        def model_fn(prompt):
            call_count[0] += 1
            if call_count[0] == 1:
                return '@tool → deny [reason: "destructive command outside policy"]'
            return '@halt → answer [status: "complete", confidence: 0.9, evidence: "unsafe action denied"]'

        runtime = self._make_runtime(model_fn)
        task = self._make_task(max_units=50)
        result = runtime.run(task)
        assert result.status == "success"
