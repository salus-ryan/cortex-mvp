"""
cortex/reward.py — Verifiable Reward Function

The core problem with proxy reward signals (parse valid, policy ok, verifier ok):
they measure protocol compliance, not goal achievement.
A model can score 1.0 on all five proxy axes and still fail the task.

This module provides a two-layer reward function:

Layer 1 — DeterministicChecker:
    Rule-based, zero-latency checks that can be verified without a model.
    These are necessary conditions for a positive reward.
    Examples: halt was reached, SCL was valid, budget not exceeded,
              no policy violations, trajectory length is reasonable.

Layer 2 — GoalVerifier:
    Semantic verification that the final state satisfies the task goal.
    Uses SCL-native comparison: goal SCL ↔ final state SCL.
    Does NOT use NL — the comparison is in SCL space.
    This is the sufficient condition for a positive reward.

The composite reward is:
    R = w_det * R_det + w_goal * R_goal
    where w_det=0.3, w_goal=0.7

The goal verifier is the primary signal. Deterministic checks are guards.

RewardStore:
    Persists all reward computations to SQLite.
    Used by the compactor to filter training data by actual goal achievement.
    Used by the learner to weight training examples.

GRPO-compatible:
    The reward function is designed to be used as a reward signal for
    Group Relative Policy Optimisation (GRPO) — the same approach used
    in DeepSeek-R1. Multiple rollouts of the same goal are compared
    relative to each other, not against an absolute threshold.
"""

from __future__ import annotations

import json
import math
import sqlite3
import hashlib
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, Callable

from cortex.scl_parser import parse as scl_parse


# ── Reward result ──────────────────────────────────────────────────────────────

@dataclass
class RewardResult:
    """Complete reward computation result."""
    task_id: str
    goal_scl: str
    final_scl: str                   # The last SCL action emitted
    final_outcome: str               # success / failure / partial / timeout

    # Deterministic checks (Layer 1)
    halt_reached: bool = False
    scl_valid: bool = False
    budget_ok: bool = False
    policy_clean: bool = False
    trajectory_length_ok: bool = False
    det_score: float = 0.0           # 0–1, mean of deterministic checks

    # Goal verification (Layer 2)
    goal_anchor_match: bool = False  # final SCL anchor matches goal anchor
    goal_relation_match: bool = False
    goal_field_overlap: float = 0.0  # Jaccard overlap of field keys
    goal_outcome_match: bool = False # outcome matches expected
    goal_score: float = 0.0          # 0–1, semantic goal achievement

    # Composite
    reward: float = 0.0              # w_det * det + w_goal * goal
    confidence: float = 0.0         # how certain we are about this reward

    # GRPO fields
    group_id: str = ""               # shared across rollouts of same goal
    relative_rank: float = 0.5      # rank within group (0=worst, 1=best)

    W_DET: float = field(default=0.3, repr=False)
    W_GOAL: float = field(default=0.7, repr=False)

    def __post_init__(self):
        self.reward = self.W_DET * self.det_score + self.W_GOAL * self.goal_score
        self.confidence = min(1.0, self.det_score * 0.4 + self.goal_score * 0.6)


# ── Deterministic checker ──────────────────────────────────────────────────────

class DeterministicChecker:
    """
    Rule-based necessary conditions for a positive reward.
    All checks are O(1) and require no model inference.
    """

    def __init__(
        self,
        max_steps: int = 20,
        min_steps: int = 1,
        max_policy_violations: int = 0,
    ):
        self.max_steps = max_steps
        self.min_steps = min_steps
        self.max_policy_violations = max_policy_violations

    def check(
        self,
        trajectory: list[dict],
        final_scl: str,
        final_outcome: str,
        budget_remaining: float = 1.0,
        policy_violations: int = 0,
    ) -> dict:
        """
        Returns a dict of boolean checks and a scalar score.
        trajectory: list of step dicts with 'scl' and 'outcome' keys
        """
        results = {}

        # 1. Halt reached
        results["halt_reached"] = final_outcome in ("success", "partial")

        # 2. Final SCL is valid
        parse_result = scl_parse(final_scl) if final_scl else None
        results["scl_valid"] = bool(parse_result and parse_result.valid)

        # 3. Budget not exhausted
        results["budget_ok"] = budget_remaining >= 0.0

        # 4. No policy violations
        results["policy_clean"] = policy_violations <= self.max_policy_violations

        # 5. Trajectory length is reasonable
        n = len(trajectory)
        results["trajectory_length_ok"] = self.min_steps <= n <= self.max_steps

        # Score = mean of checks (each is 0 or 1)
        score = sum(1 for v in results.values() if v) / len(results)
        results["det_score"] = round(score, 4)

        return results


# ── Goal verifier ──────────────────────────────────────────────────────────────

class GoalVerifier:
    """
    Semantic verification that the final state satisfies the task goal.
    Comparison is in SCL space — not NL.

    The goal is expressed as an SCL assertion:
        @state → assert [key: "task", value: "process_files", tool: "bash"]

    The final state is the last SCL action:
        @halt → answer [status: "complete", confidence: 0.9, evidence: "..."]

    Verification checks:
    1. Anchor compatibility — goal anchor is compatible with final anchor
    2. Relation compatibility — goal relation is compatible with final relation
    3. Field overlap — shared field keys between goal and final state
    4. Outcome match — final outcome matches expected outcome for goal type
    """

    # Which final anchors are compatible with which goal anchors
    ANCHOR_COMPATIBILITY = {
        "@state":  {"@halt", "@state", "@verify"},
        "@tool":   {"@halt", "@tool"},
        "@halt":   {"@halt"},
        "@memory": {"@halt", "@memory"},
        "@verify": {"@halt", "@verify"},
        "@budget": {"@halt", "@budget"},
        "@repair": {"@halt", "@repair"},
    }

    # Which final relations are compatible with which goal relations
    RELATION_COMPATIBILITY = {
        "assert": {"answer", "assert", "run"},
        "write":  {"answer", "write"},
        "call":   {"answer", "call"},
        "check":  {"answer", "check", "report"},
        "store":  {"answer", "store"},
        "answer": {"answer"},
        "fail":   {"fail", "escalate"},
        "defer":  {"defer"},
    }

    def verify(
        self,
        goal_scl: str,
        final_scl: str,
        final_outcome: str,
        expected_outcome: str = "success",
    ) -> dict:
        """
        Returns a dict of verification results and a scalar goal_score.
        """
        results = {}

        # Parse both
        goal_parse = scl_parse(goal_scl) if goal_scl else None
        final_parse = scl_parse(final_scl) if final_scl else None

        if not goal_parse or not goal_parse.valid:
            # Can't verify against invalid goal — neutral score
            return {
                "goal_anchor_match": False,
                "goal_relation_match": False,
                "goal_field_overlap": 0.0,
                "goal_outcome_match": final_outcome == expected_outcome,
                "goal_score": 0.3,
            }

        if not final_parse or not final_parse.valid:
            return {
                "goal_anchor_match": False,
                "goal_relation_match": False,
                "goal_field_overlap": 0.0,
                "goal_outcome_match": False,
                "goal_score": 0.0,
            }

        goal_action = goal_parse.action
        final_action = final_parse.action

        # 1. Anchor compatibility
        compatible_anchors = self.ANCHOR_COMPATIBILITY.get(goal_action.anchor, set())
        results["goal_anchor_match"] = final_action.anchor in compatible_anchors

        # 2. Relation compatibility
        compatible_rels = self.RELATION_COMPATIBILITY.get(goal_action.relation, set())
        results["goal_relation_match"] = final_action.relation in compatible_rels

        # 3. Field overlap (Jaccard on field keys)
        goal_keys = set(goal_action.fields.keys())
        final_keys = set(final_action.fields.keys())
        if goal_keys or final_keys:
            overlap = len(goal_keys & final_keys) / len(goal_keys | final_keys)
        else:
            overlap = 1.0  # both empty = perfect match
        results["goal_field_overlap"] = round(overlap, 4)

        # 4. Outcome match
        results["goal_outcome_match"] = final_outcome == expected_outcome

        # Composite goal score
        # Anchor match is most important (0.4), then outcome (0.3),
        # then relation (0.2), then field overlap (0.1)
        score = (
            0.4 * float(results["goal_anchor_match"]) +
            0.3 * float(results["goal_outcome_match"]) +
            0.2 * float(results["goal_relation_match"]) +
            0.1 * results["goal_field_overlap"]
        )
        results["goal_score"] = round(score, 4)

        return results


# ── Composite reward function ──────────────────────────────────────────────────

class VerifiableRewardFunction:
    """
    The complete verifiable reward function.

    R = 0.3 * R_det + 0.7 * R_goal

    The 0.7 weight on goal achievement is the key design decision.
    It means the model is primarily rewarded for solving the problem,
    not for following the protocol. Protocol compliance is a guard (0.3),
    not the objective.
    """

    def __init__(
        self,
        max_steps: int = 20,
        w_det: float = 0.3,
        w_goal: float = 0.7,
    ):
        assert abs(w_det + w_goal - 1.0) < 1e-6, "Weights must sum to 1"
        self.checker = DeterministicChecker(max_steps=max_steps)
        self.verifier = GoalVerifier()
        self.w_det = w_det
        self.w_goal = w_goal

    def compute(
        self,
        task_id: str,
        goal_scl: str,
        trajectory: list[dict],
        final_scl: str,
        final_outcome: str,
        budget_remaining: float = 1.0,
        policy_violations: int = 0,
        expected_outcome: str = "success",
        group_id: str = "",
    ) -> RewardResult:
        """
        Compute the full verifiable reward for a completed trajectory.

        trajectory: list of dicts, each with keys: 'scl', 'outcome', 'step'
        """
        # Layer 1: deterministic checks
        det = self.checker.check(
            trajectory=trajectory,
            final_scl=final_scl,
            final_outcome=final_outcome,
            budget_remaining=budget_remaining,
            policy_violations=policy_violations,
        )

        # Layer 2: goal verification
        goal = self.verifier.verify(
            goal_scl=goal_scl,
            final_scl=final_scl,
            final_outcome=final_outcome,
            expected_outcome=expected_outcome,
        )

        # Composite
        det_score = det["det_score"]
        goal_score = goal["goal_score"]
        reward = self.w_det * det_score + self.w_goal * goal_score
        confidence = min(1.0, det_score * 0.4 + goal_score * 0.6)

        result = RewardResult(
            task_id=task_id,
            goal_scl=goal_scl,
            final_scl=final_scl,
            final_outcome=final_outcome,
            halt_reached=det["halt_reached"],
            scl_valid=det["scl_valid"],
            budget_ok=det["budget_ok"],
            policy_clean=det["policy_clean"],
            trajectory_length_ok=det["trajectory_length_ok"],
            det_score=det_score,
            goal_anchor_match=goal["goal_anchor_match"],
            goal_relation_match=goal["goal_relation_match"],
            goal_field_overlap=goal["goal_field_overlap"],
            goal_outcome_match=goal["goal_outcome_match"],
            goal_score=goal_score,
            reward=round(reward, 4),
            confidence=round(confidence, 4),
            group_id=group_id or task_id,
            W_DET=self.w_det,
            W_GOAL=self.w_goal,
        )

        return result

    def compute_group_ranks(self, results: list[RewardResult]) -> list[RewardResult]:
        """
        Compute relative ranks within a group of rollouts (for GRPO).
        Rank 0.0 = worst in group, 1.0 = best in group.
        """
        if not results:
            return results
        rewards = [r.reward for r in results]
        min_r, max_r = min(rewards), max(rewards)
        for r in results:
            if max_r > min_r:
                r.relative_rank = round((r.reward - min_r) / (max_r - min_r), 4)
            else:
                r.relative_rank = 0.5
        return results


# ── Reward store ───────────────────────────────────────────────────────────────

class RewardStore:
    """
    Persists reward computations to SQLite.
    Used by compactor to filter by actual goal achievement.
    Used by learner to weight training examples.
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS rewards (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id     TEXT NOT NULL,
        group_id    TEXT NOT NULL,
        goal_scl    TEXT,
        final_scl   TEXT,
        final_outcome TEXT,
        det_score   REAL,
        goal_score  REAL,
        reward      REAL,
        confidence  REAL,
        relative_rank REAL DEFAULT 0.5,
        halt_reached  INTEGER,
        scl_valid     INTEGER,
        budget_ok     INTEGER,
        policy_clean  INTEGER,
        goal_anchor_match   INTEGER,
        goal_relation_match INTEGER,
        goal_field_overlap  REAL,
        goal_outcome_match  INTEGER,
        created_at  REAL DEFAULT (strftime('%s','now'))
    );
    CREATE INDEX IF NOT EXISTS idx_rewards_task ON rewards(task_id);
    CREATE INDEX IF NOT EXISTS idx_rewards_group ON rewards(group_id);
    CREATE INDEX IF NOT EXISTS idx_rewards_reward ON rewards(reward);
    """

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(self.SCHEMA)

    def save(self, result: RewardResult):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO rewards (
                    task_id, group_id, goal_scl, final_scl, final_outcome,
                    det_score, goal_score, reward, confidence, relative_rank,
                    halt_reached, scl_valid, budget_ok, policy_clean,
                    goal_anchor_match, goal_relation_match, goal_field_overlap,
                    goal_outcome_match
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                result.task_id, result.group_id, result.goal_scl,
                result.final_scl, result.final_outcome,
                result.det_score, result.goal_score, result.reward,
                result.confidence, result.relative_rank,
                int(result.halt_reached), int(result.scl_valid),
                int(result.budget_ok), int(result.policy_clean),
                int(result.goal_anchor_match), int(result.goal_relation_match),
                result.goal_field_overlap, int(result.goal_outcome_match),
            ))

    def get_by_task(self, task_id: str) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM rewards WHERE task_id=? ORDER BY created_at",
                (task_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_high_reward(self, threshold: float = 0.7) -> list[dict]:
        """Get all reward results above threshold — used by compactor."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM rewards WHERE reward >= ? ORDER BY reward DESC",
                (threshold,)
            ).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("""
                SELECT
                    COUNT(*) as total,
                    AVG(reward) as mean_reward,
                    AVG(goal_score) as mean_goal_score,
                    AVG(det_score) as mean_det_score,
                    SUM(halt_reached) as halts,
                    SUM(scl_valid) as valid_scl,
                    SUM(goal_outcome_match) as outcome_matches
                FROM rewards
            """).fetchone()
        return dict(zip(
            ["total", "mean_reward", "mean_goal_score", "mean_det_score",
             "halts", "valid_scl", "outcome_matches"],
            row
        )) if row else {}

    def update_group_ranks(self, group_id: str, ranks: list[tuple[int, float]]):
        """Update relative_rank for a group after GRPO ranking."""
        with sqlite3.connect(self.db_path) as conn:
            for row_id, rank in ranks:
                conn.execute(
                    "UPDATE rewards SET relative_rank=? WHERE id=?",
                    (rank, row_id)
                )


# ── GRPO reward scorer ─────────────────────────────────────────────────────────

class GRPORewardScorer:
    """
    Group Relative Policy Optimisation reward scorer.

    For each task goal, multiple rollouts are generated and compared
    relative to each other. The reward signal is the relative rank
    within the group, not an absolute score.

    This is the same approach used in DeepSeek-R1 and is more stable
    than absolute reward signals for small models.

    Usage:
        scorer = GRPORewardScorer(reward_fn, store)
        group = scorer.score_group(goal_scl, rollouts)
        # group is a list of RewardResult with relative_rank set
    """

    def __init__(
        self,
        reward_fn: VerifiableRewardFunction,
        store: Optional[RewardStore] = None,
    ):
        self.reward_fn = reward_fn
        self.store = store

    def score_group(
        self,
        goal_scl: str,
        rollouts: list[dict],
        expected_outcome: str = "success",
    ) -> list[RewardResult]:
        """
        Score a group of rollouts for the same goal.

        rollouts: list of dicts, each with:
            - task_id: str
            - trajectory: list[dict]
            - final_scl: str
            - final_outcome: str
            - budget_remaining: float (optional)
            - policy_violations: int (optional)
        """
        group_id = hashlib.sha256(goal_scl.encode()).hexdigest()[:12]
        results = []

        for rollout in rollouts:
            result = self.reward_fn.compute(
                task_id=rollout.get("task_id", group_id),
                goal_scl=goal_scl,
                trajectory=rollout.get("trajectory", []),
                final_scl=rollout.get("final_scl", ""),
                final_outcome=rollout.get("final_outcome", "failure"),
                budget_remaining=rollout.get("budget_remaining", 1.0),
                policy_violations=rollout.get("policy_violations", 0),
                expected_outcome=expected_outcome,
                group_id=group_id,
            )
            results.append(result)

        # Compute relative ranks
        results = self.reward_fn.compute_group_ranks(results)

        # Persist
        if self.store:
            for result in results:
                self.store.save(result)

        return results

    def advantage(self, result: RewardResult, baseline: float = 0.5) -> float:
        """
        Compute the GRPO advantage for a single result.
        advantage = relative_rank - baseline
        Positive = better than group average, negative = worse.
        """
        return result.relative_rank - baseline
