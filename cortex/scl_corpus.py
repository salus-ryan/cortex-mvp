"""
cortex/scl_corpus.py — SCL-native pretraining corpus generator

The core principle: SCL is the model's NATIVE representational language.
Natural language is the annotation. SCL is the substrate.

A standard fine-tuned model learns:
    "given this NL prompt → emit this SCL string"
    The model thinks in NL, translates to SCL. SCL is a foreign language.

An SCL-native model learns:
    "given this SCL goal state → emit the next SCL transition"
    The model thinks in SCL. NL is a lossy projection of SCL, not the reverse.

This corpus generator produces three types of training examples:

1. SCL-CONTINUATION (primary):
   Input:  partial SCL trajectory (goal + steps so far)
   Output: next valid SCL action
   The model learns to reason about governed state transitions in SCL space.

2. SCL-COMPRESSION (secondary):
   Input:  natural language description of intent
   Output: the minimal SCL expression that captures it
   The model learns that NL is a verbose projection of SCL, not the reverse.

3. SCL-REFLECTION (tertiary):
   Input:  SCL trajectory + outcome
   Output: SCL-encoded diagnosis of what went wrong / right
   The model learns to reason about its own governance decisions in SCL.

The ratio is 60% continuation, 25% compression, 15% reflection.
This ratio encodes the epistemic priority: SCL reasoning first, NL translation second.
"""

from __future__ import annotations

import json
import random
import hashlib
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from cortex.scl_emitter import SCLEmitter
from cortex.scl_parser import parse as scl_parse

# ── SCL vocabulary ─────────────────────────────────────────────────────────────

ANCHORS = ["@tool", "@halt", "@memory", "@state", "@repair", "@verify", "@budget"]

RELATIONS = {
    "@tool":   ["call", "deny"],
    "@halt":   ["answer", "fail", "defer"],
    "@memory": ["read", "write", "compress", "ignore"],
    "@state":  ["update", "snapshot"],
    "@repair": ["rollback", "patch", "diagnose"],
    "@verify": ["run", "assert"],
    "@budget": ["spend", "check", "snapshot"],
}

TOOLS = ["bash", "python", "search", "read_file", "write_file", "list_dir",
         "http_get", "http_post", "sql_query", "grep", "diff", "patch"]

RISK_TIERS = ["read_only", "write_limited", "verify", "memory", "deny", "halt"]

OUTCOMES = ["success", "failure", "partial", "timeout", "policy_denied"]

# Task families — each maps to a natural SCL reasoning pattern
TASK_FAMILIES = {
    "file_ops":       ("file system operations", ["read_file", "write_file", "list_dir", "bash"]),
    "code_exec":      ("code execution and testing", ["python", "bash"]),
    "search_answer":  ("information retrieval", ["search", "http_get"]),
    "data_transform": ("data processing and transformation", ["python", "sql_query"]),
    "system_check":   ("system state verification", ["bash", "grep"]),
    "repair":         ("error recovery and rollback", ["bash", "python"]),
    "multi_step":     ("multi-step reasoning chains", ["bash", "python", "search"]),
    "verify_state":   ("state assertion and verification", ["bash", "grep", "python"]),
}

# SCL reasoning patterns — the cognitive primitives
REASONING_PATTERNS = [
    # Check budget → Act → Verify → Halt
    ["@budget → check", "@tool → call", "@verify → run", "@halt → answer"],
    # Read memory → Act → Write memory → Halt
    ["@memory → read", "@tool → call", "@memory → write", "@halt → answer"],
    # Snapshot state → Act → Update state → Halt
    ["@state → snapshot", "@tool → call", "@state → update", "@halt → answer"],
    # Attempt → Fail → Repair → Retry → Halt
    ["@tool → call", "@repair → rollback", "@tool → call", "@halt → answer"],
    # Verify precondition → Act → Verify postcondition → Halt
    ["@verify → assert", "@tool → call", "@verify → run", "@halt → answer"],
    # Diagnose → Patch → Verify → Halt
    ["@repair → diagnose", "@repair → patch", "@verify → run", "@halt → answer"],
]


# ── Data structures ────────────────────────────────────────────────────────────

@dataclass
class SCLStep:
    """A single governed state transition in SCL."""
    anchor: str
    relation: str
    fields: dict
    nl_annotation: str = ""          # NL is annotation, not substrate
    outcome: str = "success"
    reward: float = 1.0

    def to_scl(self) -> str:
        """Emit canonical SCL string directly — no emitter helper methods needed."""
        def _fields_str(d: dict) -> str:
            parts = []
            for k, v in d.items():
                if isinstance(v, (int, float)):
                    parts.append(f'{k}: {v}')
                else:
                    parts.append(f'{k}: "{v}"')
            return ", ".join(parts)

        if self.anchor == "@tool" and self.relation == "call":
            name = self.fields.get("name", "bash")
            args = self.fields.get("args", "")
            risk = self.fields.get("risk", "read_only")
            return f'@tool → call [name: "{name}", args: "{args}", risk: "{risk}"]'
        elif self.anchor == "@halt":
            status = self.fields.get("status", "complete")
            evidence = self.fields.get("evidence", "task complete")
            confidence = float(self.fields.get("confidence", 0.9))
            relation = self.relation if self.relation in ("answer", "fail", "defer") else "answer"
            return f'@halt → {relation} [status: "{status}", confidence: {confidence}, evidence: "{evidence}"]'
        elif self.anchor == "@memory":
            key = self.fields.get("key", "result")
            value = self.fields.get("value", "")
            return f'@memory → {self.relation} [key: "{key}", value: "{value}"]'
        elif self.anchor == "@verify":
            vtype = self.fields.get("type", "schema")
            target = self.fields.get("target", "state")
            return f'@verify → {self.relation} [type: "{vtype}", target: "{target}"]'
        elif self.anchor == "@budget":
            if self.fields:
                fs = _fields_str(self.fields)
                return f'@budget → {self.relation} [{fs}]'
            return f'@budget → {self.relation} []'
        elif self.anchor == "@repair":
            target = self.fields.get("target", "last_action")
            reason = self.fields.get("reason", "tool_error")
            return f'@repair → {self.relation} [target: "{target}", reason: "{reason}"]'
        elif self.anchor == "@state":
            if self.relation == "update":
                phase = self.fields.get("phase", "execute")
                conf = self.fields.get("confidence", 0.8)
                return f'@state → update [phase: "{phase}", confidence: {conf}]'
            else:  # snapshot
                task_id = self.fields.get("task_id", "task_0")
                return f'@state → snapshot [task_id: "{task_id}"]'
        else:
            fs = _fields_str(self.fields) if self.fields else ""
            return f'{self.anchor} → {self.relation} [{fs}]'


@dataclass
class SCLTrajectory:
    """A complete governed task trajectory in SCL."""
    task_id: str
    goal_scl: str                    # The goal expressed in SCL, not NL
    goal_nl: str                     # NL annotation of the goal
    steps: list[SCLStep] = field(default_factory=list)
    final_outcome: str = "success"
    total_reward: float = 0.0
    task_family: str = "general"

    def to_scl_context(self, up_to_step: int = -1) -> str:
        """Render the trajectory as an SCL context string."""
        steps = self.steps if up_to_step < 0 else self.steps[:up_to_step]
        lines = [f"GOAL: {self.goal_scl}"]
        for i, step in enumerate(steps):
            lines.append(f"STEP[{i}]: {step.to_scl()}")
            if step.outcome != "success":
                lines.append(f"OUTCOME[{i}]: {step.outcome}")
        return "\n".join(lines)


# ── Corpus generators ──────────────────────────────────────────────────────────

class SCLCorpusGenerator:
    """
    Generates SCL-native training examples.

    The three example types encode a specific cognitive priority:
    - Continuation: the model reasons in SCL space (primary)
    - Compression: the model translates NL → SCL (secondary)
    - Reflection: the model diagnoses in SCL (tertiary)
    """

    def __init__(self, seed: int = 42):
        self.rng = random.Random(seed)
        self.emitter = SCLEmitter()

    # ── Goal generation ────────────────────────────────────────────────────────

    def _make_goal_scl(self, family: str, tools: list[str]) -> tuple[str, str]:
        """Generate a goal expressed in SCL form with NL annotation."""
        tool = self.rng.choice(tools)
        goals = {
            "file_ops": (
                f'@state → assert [key: "task", value: "process_files", tool: "{tool}"]',
                f"Process files using {tool}"
            ),
            "code_exec": (
                f'@state → assert [key: "task", value: "execute_code", tool: "{tool}"]',
                f"Execute and verify code using {tool}"
            ),
            "search_answer": (
                f'@state → assert [key: "task", value: "retrieve_answer", tool: "{tool}"]',
                f"Retrieve and verify information using {tool}"
            ),
            "data_transform": (
                f'@state → assert [key: "task", value: "transform_data", tool: "{tool}"]',
                f"Transform data using {tool}"
            ),
            "system_check": (
                f'@state → assert [key: "task", value: "verify_system", tool: "{tool}"]',
                f"Verify system state using {tool}"
            ),
            "repair": (
                f'@state → assert [key: "task", value: "repair_state", tool: "{tool}"]',
                f"Detect and repair error state using {tool}"
            ),
            "multi_step": (
                f'@state → assert [key: "task", value: "multi_step_reasoning", tool: "{tool}"]',
                f"Complete multi-step reasoning task using {tool}"
            ),
            "verify_state": (
                f'@state → assert [key: "task", value: "verify_assertions", tool: "{tool}"]',
                f"Verify state assertions using {tool}"
            ),
        }
        return goals.get(family, (
            f'@state → assert [key: "task", value: "general", tool: "{tool}"]',
            f"Complete task using {tool}"
        ))

    # ── Step generation ────────────────────────────────────────────────────────

    def _make_step(self, anchor: str, relation: str,
                   family: str, tools: list[str]) -> SCLStep:
        tool = self.rng.choice(tools)
        args_map = {
            "bash":       ["ls -la", "cat file.txt", "echo done", "pwd", "find . -name '*.py'"],
            "python":     ["print('ok')", "import os; print(os.getcwd())", "x = 1 + 1"],
            "search":     ["latest results", "documentation", "error message"],
            "read_file":  ["config.json", "data.csv", "README.md"],
            "write_file": ["output.txt", "result.json", "log.txt"],
            "list_dir":   [".", "/tmp", "src/"],
            "grep":       ["error", "TODO", "def "],
            "http_get":   ["https://api.example.com/data"],
            "sql_query":  ["SELECT * FROM results LIMIT 10"],
        }
        args = self.rng.choice(args_map.get(tool, ["default_arg"]))

        fields = {}
        nl = ""

        if anchor == "@tool":
            if relation == "call":
                risk = self.rng.choice(["read_only", "write_limited"])
                fields = {"name": tool, "args": args, "risk": risk}
                nl = f"Call {tool} with: {args}"
            else:  # deny
                fields = {"name": tool, "reason": "policy_violation"}
                nl = f"Deny tool {tool}: policy violation"
        elif anchor == "@halt":
            status = self.rng.choice(["complete", "failed"])
            evidence = self.rng.choice([
                "all assertions verified", "task objective achieved",
                "output matches expected", "state consistent"
            ])
            conf = round(self.rng.uniform(0.75, 0.99), 2)
            fields = {"status": status, "confidence": conf, "evidence": evidence}
            nl = f"Halt: {status} — {evidence} (confidence {conf})"
        elif anchor == "@memory":
            key = self.rng.choice(["last_result", "intermediate", "context", "state"])
            value = self.rng.choice(["success", "pending", "computed", "verified"])
            fields = {"key": key, "value": value}
            nl = f"Memory {relation}: {key} = {value}"
        elif anchor == "@verify":
            # @verify requires type and target fields
            vtype = self.rng.choice(["schema", "unit_test", "policy", "lint", "git_diff"])
            target = self.rng.choice(["output.json", "src/main.py", "data.csv", "state"])
            fields = {"type": vtype, "target": target}
            nl = f"Verify {vtype} on {target}"
        elif anchor == "@budget":
            if relation == "spend":
                fields = {"units": self.rng.randint(1, 10), "reason": "tool_call"}
            else:
                fields = {}
            nl = f"Budget {relation}"
        elif anchor == "@repair":
            target = self.rng.choice(["last_action", "last_write", "last_call"])
            reason = self.rng.choice(["policy_violation", "tool_error", "state_inconsistent"])
            fields = {"target": target, "reason": reason}
            nl = f"Repair {relation}: {target} — {reason}"
        elif anchor == "@state":
            # @state only allows update/snapshot with specific fields
            if relation == "update":
                phase = self.rng.choice(["init", "diagnose", "plan", "execute", "verify", "repair", "halt"])
                fields = {"phase": phase, "confidence": round(self.rng.uniform(0.5, 1.0), 2)}
                nl = f"State update: phase={phase}"
            else:  # snapshot
                fields = {"task_id": f"task_{self.rng.randint(1, 999)}"}
                nl = "State snapshot"

        return SCLStep(anchor=anchor, relation=relation,
                       fields=fields, nl_annotation=nl)

    # ── Trajectory generation ──────────────────────────────────────────────────

    def generate_trajectory(self, family: str, n_steps: int = 3) -> SCLTrajectory:
        desc, tools = TASK_FAMILIES[family]
        goal_scl, goal_nl = self._make_goal_scl(family, tools)
        tid = hashlib.sha256(f"{family}{goal_scl}{self.rng.random()}".encode()).hexdigest()[:12]

        traj = SCLTrajectory(
            task_id=tid,
            goal_scl=goal_scl,
            goal_nl=goal_nl,
            task_family=family,
        )

        # Choose a reasoning pattern
        pattern = self.rng.choice(REASONING_PATTERNS)
        steps_to_use = pattern[:n_steps] if n_steps <= len(pattern) else pattern

        for pattern_step in steps_to_use:
            # Parse the pattern hint
            parts = pattern_step.split(" → ")
            anchor = parts[0].strip()
            relation_hint = parts[1].split(" ")[0].strip() if len(parts) > 1 else "call"

            # Resolve to valid relation
            valid_rels = RELATIONS.get(anchor, ["call"])
            relation = relation_hint if relation_hint in valid_rels else valid_rels[0]

            step = self._make_step(anchor, relation, family, tools)
            traj.steps.append(step)

        traj.final_outcome = "success"
        traj.total_reward = 1.0
        return traj

    # ── Example type 1: SCL-CONTINUATION ──────────────────────────────────────

    def make_continuation_example(self, traj: SCLTrajectory,
                                   step_idx: int) -> dict:
        """
        Input:  SCL goal + steps 0..step_idx-1
        Output: SCL step at step_idx

        This is the primary training signal. The model learns to predict
        the next governed transition given the current SCL context.
        """
        if step_idx >= len(traj.steps):
            return {}

        context = traj.to_scl_context(up_to_step=step_idx)
        target_step = traj.steps[step_idx]
        target_scl = target_step.to_scl()

        # Validate the target — skip invalid steps
        result = scl_parse(target_scl)
        if not result.valid:
            return {}

        prompt = (
            f"[SCL-CONTEXT]\n{context}\n"
            f"[NEXT-TRANSITION]"
        )
        completion = f"\n{target_scl}"

        return {
            "type": "continuation",
            "prompt": prompt,
            "completion": completion,
            "task_family": traj.task_family,
            "step_idx": step_idx,
            "total_steps": len(traj.steps),
            "scl_valid": result.valid,
            "quality": 1.0,
        }

    # ── Example type 2: SCL-COMPRESSION ───────────────────────────────────────

    def make_compression_example(self, traj: SCLTrajectory) -> dict:
        """
        Input:  natural language description of intent
        Output: the minimal SCL expression

        NL is the verbose, ambiguous projection.
        SCL is the precise, governed representation.
        The model learns that compression is the direction of truth.
        """
        step = self.rng.choice(traj.steps)
        target_scl = step.to_scl()

        # Validate — skip invalid examples
        result = scl_parse(target_scl)
        if not result.valid:
            return {}

        # NL is deliberately verbose and ambiguous — SCL is the compression
        nl_expansions = {
            "@tool": [
                f"I need to {step.nl_annotation.lower()}",
                f"Please {step.nl_annotation.lower()} for me",
                f"Can you {step.nl_annotation.lower()}?",
                f"Execute: {step.nl_annotation}",
            ],
            "@halt": [
                f"I think we're done. {step.nl_annotation}",
                f"The task appears complete. {step.nl_annotation}",
                f"We should stop here because {step.nl_annotation.lower()}",
            ],
            "@memory": [
                f"Remember this: {step.nl_annotation}",
                f"Store for later: {step.nl_annotation}",
            ],
            "@verify": [
                f"Check that {step.nl_annotation.lower()}",
                f"Make sure {step.nl_annotation.lower()}",
                f"Verify: {step.nl_annotation}",
            ],
            "@budget": [
                f"How much budget is left?",
                f"Check remaining resources",
            ],
            "@repair": [
                f"Something went wrong, {step.nl_annotation.lower()}",
                f"Undo the last action: {step.nl_annotation}",
            ],
            "@state": [
                f"Update the state: {step.nl_annotation}",
                f"Record: {step.nl_annotation}",
            ],
        }

        nl_options = nl_expansions.get(step.anchor, [step.nl_annotation])
        nl_input = self.rng.choice(nl_options)

        prompt = (
            f"[NL-INTENT]\n{nl_input}\n"
            f"[SCL-COMPRESSION]"
        )
        completion = f"\n{target_scl}"

        return {
            "type": "compression",
            "prompt": prompt,
            "completion": completion,
            "task_family": traj.task_family,
            "nl_input": nl_input,
            "scl_valid": result.valid,
            "quality": 1.0,
        }

    # ── Example type 3: SCL-REFLECTION ────────────────────────────────────────

    def make_reflection_example(self, outcome: str = "success") -> dict:
        """
        Input:  SCL trajectory + outcome
        Output: SCL-encoded diagnosis

        The model learns to reason about its own governance decisions in SCL.
        Reflection is encoded in SCL, not NL — the model's introspection
        is governed by the same authority model as its actions.
        """
        family = self.rng.choice(list(TASK_FAMILIES.keys()))
        traj = self.generate_trajectory(family, n_steps=self.rng.randint(2, 4))

        if outcome == "failure":
            # Inject a policy violation or premature halt
            traj.final_outcome = "failure"
            traj.total_reward = 0.0
            diagnosis_scl = (
                f'@state → update [phase: "repair", confidence: 0.2]\n'
                f'@repair → diagnose [target: "last_action", reason: "governance_contract_violated"]'
            )
            reflection_nl = "Task failed due to governance violation"
        elif outcome == "partial":
            traj.final_outcome = "partial"
            traj.total_reward = 0.5
            diagnosis_scl = (
                f'@state → update [phase: "repair", confidence: 0.5]\n'
                f'@memory → write [key: "incomplete_steps", value: "retry_required"]'
            )
            reflection_nl = "Task partially completed, retry required"
        else:
            traj.final_outcome = "success"
            traj.total_reward = 1.0
            diagnosis_scl = (
                f'@state → update [phase: "halt", confidence: 0.95]\n'
                f'@memory → write [key: "successful_pattern", value: "{family}"]'
            )
            reflection_nl = "Task succeeded, pattern stored"

        context = traj.to_scl_context()
        prompt = (
            f"[SCL-TRAJECTORY]\n{context}\n"
            f"[OUTCOME]: {outcome}\n"
            f"[SCL-REFLECTION]"
        )
        completion = f"\n{diagnosis_scl}"

        return {
            "type": "reflection",
            "prompt": prompt,
            "completion": completion,
            "task_family": family,
            "outcome": outcome,
            "reflection_nl": reflection_nl,
            "quality": 1.0 if outcome == "success" else 0.6,
        }

    # ── Batch generation ───────────────────────────────────────────────────────

    def generate_corpus(
        self,
        n_total: int = 1000,
        continuation_ratio: float = 0.60,
        compression_ratio: float = 0.25,
        reflection_ratio: float = 0.15,
    ) -> list[dict]:
        """
        Generate a full SCL-native pretraining corpus.

        Ratio encodes epistemic priority:
        - 60% continuation: SCL reasoning is primary
        - 25% compression: NL→SCL translation is secondary
        - 15% reflection: SCL introspection is tertiary
        """
        assert abs(continuation_ratio + compression_ratio + reflection_ratio - 1.0) < 1e-6

        n_continuation = int(n_total * continuation_ratio)
        n_compression  = int(n_total * compression_ratio)
        n_reflection   = n_total - n_continuation - n_compression

        examples = []

        # Continuation examples
        families = list(TASK_FAMILIES.keys())
        for _ in range(n_continuation):
            family = self.rng.choice(families)
            n_steps = self.rng.randint(2, 5)
            traj = self.generate_trajectory(family, n_steps=n_steps)
            step_idx = self.rng.randint(0, len(traj.steps) - 1)
            ex = self.make_continuation_example(traj, step_idx)
            if ex:
                examples.append(ex)

        # Compression examples
        for _ in range(n_compression):
            family = self.rng.choice(families)
            traj = self.generate_trajectory(family, n_steps=self.rng.randint(2, 4))
            ex = self.make_compression_example(traj)
            if ex:
                examples.append(ex)

        # Reflection examples
        outcomes = ["success"] * 6 + ["failure"] * 2 + ["partial"] * 2
        for _ in range(n_reflection):
            outcome = self.rng.choice(outcomes)
            ex = self.make_reflection_example(outcome)
            if ex:
                examples.append(ex)

        self.rng.shuffle(examples)
        return examples

    def save_corpus(self, examples: list[dict], path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            for ex in examples:
                f.write(json.dumps(ex) + "\n")
        return path

    def generate_and_save(
        self,
        output_path: Path,
        n_total: int = 1000,
    ) -> dict:
        examples = self.generate_corpus(n_total=n_total)
        path = self.save_corpus(examples, output_path)

        by_type = {}
        for ex in examples:
            t = ex.get("type", "unknown")
            by_type[t] = by_type.get(t, 0) + 1

        return {
            "path": str(path),
            "total": len(examples),
            "by_type": by_type,
        }
