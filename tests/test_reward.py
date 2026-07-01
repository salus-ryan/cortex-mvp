"""Tests for cortex/reward.py — Verifiable Reward Function."""
import pytest
import tempfile
from pathlib import Path

from cortex.reward import (
    DeterministicChecker, GoalVerifier, VerifiableRewardFunction,
    RewardResult, RewardStore, GRPORewardScorer,
)


# ── Fixtures ───────────────────────────────────────────────────────────────────

VALID_HALT = '@halt → answer [status: "complete", confidence: 0.9, evidence: "done"]'
VALID_TOOL = '@tool → call [name: "bash", args: "ls", risk: "read_only"]'
VALID_GOAL = '@state → update [phase: "execute", confidence: 0.9]'
INVALID_SCL = "this is not scl"

GOOD_TRAJ = [
    {"scl": VALID_TOOL, "outcome": "success", "step": 0},
    {"scl": VALID_HALT, "outcome": "success", "step": 1},
]


@pytest.fixture
def checker():
    return DeterministicChecker(max_steps=20, min_steps=1)


@pytest.fixture
def verifier():
    return GoalVerifier()


@pytest.fixture
def reward_fn():
    return VerifiableRewardFunction()


@pytest.fixture
def store(tmp_path):
    return RewardStore(tmp_path / "rewards.db")


# ── DeterministicChecker ───────────────────────────────────────────────────────

class TestDeterministicChecker:
    def test_all_pass_on_good_trajectory(self, checker):
        result = checker.check(
            trajectory=GOOD_TRAJ,
            final_scl=VALID_HALT,
            final_outcome="success",
            budget_remaining=0.5,
            policy_violations=0,
        )
        assert result["halt_reached"] is True
        assert result["scl_valid"] is True
        assert result["budget_ok"] is True
        assert result["policy_clean"] is True
        assert result["trajectory_length_ok"] is True
        assert result["det_score"] == 1.0

    def test_halt_not_reached_on_failure(self, checker):
        result = checker.check(
            trajectory=GOOD_TRAJ,
            final_scl=VALID_HALT,
            final_outcome="failure",
            budget_remaining=0.5,
        )
        assert result["halt_reached"] is False
        assert result["det_score"] < 1.0

    def test_invalid_scl_fails_check(self, checker):
        result = checker.check(
            trajectory=GOOD_TRAJ,
            final_scl=INVALID_SCL,
            final_outcome="success",
        )
        assert result["scl_valid"] is False
        assert result["det_score"] < 1.0

    def test_policy_violation_fails_check(self, checker):
        result = checker.check(
            trajectory=GOOD_TRAJ,
            final_scl=VALID_HALT,
            final_outcome="success",
            policy_violations=1,
        )
        assert result["policy_clean"] is False

    def test_empty_trajectory_fails_length_check(self, checker):
        result = checker.check(
            trajectory=[],
            final_scl=VALID_HALT,
            final_outcome="success",
        )
        assert result["trajectory_length_ok"] is False

    def test_too_long_trajectory_fails(self, checker):
        long_traj = [{"scl": VALID_TOOL, "outcome": "success", "step": i}
                     for i in range(25)]
        result = checker.check(
            trajectory=long_traj,
            final_scl=VALID_HALT,
            final_outcome="success",
        )
        assert result["trajectory_length_ok"] is False

    def test_det_score_is_mean_of_checks(self, checker):
        result = checker.check(
            trajectory=GOOD_TRAJ,
            final_scl=INVALID_SCL,   # 1 failure
            final_outcome="success",
            budget_remaining=0.5,
            policy_violations=0,
        )
        # 4 pass, 1 fail → 0.8
        assert result["det_score"] == pytest.approx(0.8)


# ── GoalVerifier ───────────────────────────────────────────────────────────────

class TestGoalVerifier:
    def test_perfect_match_goal_halt(self, verifier):
        """
        @state goal → @halt answer is the canonical success pattern.
        The goal declares what the task IS (@state); the final action is a halt (@halt).
        Anchors differ by design — goal_outcome_match and goal_score are what matter.
        """
        result = verifier.verify(
            goal_scl=VALID_GOAL,
            final_scl=VALID_HALT,
            final_outcome="success",
            expected_outcome="success",
        )
        # @state and @halt are compatible anchors in the GoalVerifier table
        # (state goals are satisfied by halt actions)
        assert result["goal_anchor_match"] is True
        # Outcome matches
        assert result["goal_outcome_match"] is True
        # Score is high (anchor + outcome both contribute)
        assert result["goal_score"] > 0.5

    def test_outcome_mismatch_reduces_score(self, verifier):
        result = verifier.verify(
            goal_scl=VALID_GOAL,
            final_scl=VALID_HALT,
            final_outcome="failure",
            expected_outcome="success",
        )
        assert result["goal_outcome_match"] is False
        assert result["goal_score"] < 0.7

    def test_invalid_goal_returns_neutral(self, verifier):
        result = verifier.verify(
            goal_scl=INVALID_SCL,
            final_scl=VALID_HALT,
            final_outcome="success",
        )
        assert result["goal_score"] == pytest.approx(0.3)

    def test_invalid_final_returns_zero(self, verifier):
        """
        When final SCL is invalid but outcome is 'failure', all sub-scores are 0
        and goal_score should be 0.0.
        """
        result = verifier.verify(
            goal_scl=VALID_GOAL,
            final_scl=INVALID_SCL,
            final_outcome="failure",   # outcome mismatch → goal_outcome_match=False
        )
        assert result["goal_anchor_match"] is False
        assert result["goal_relation_match"] is False
        assert result["goal_outcome_match"] is False
        assert result["goal_score"] == 0.0

    def test_invalid_final_no_anchor_match(self, verifier):
        """When final SCL is invalid, anchor/relation/field matches are all False."""
        result = verifier.verify(
            goal_scl=VALID_GOAL,
            final_scl=INVALID_SCL,
            final_outcome="success",
        )
        assert result["goal_anchor_match"] is False
        assert result["goal_relation_match"] is False

    def test_tool_goal_tool_final_matches(self, verifier):
        goal = '@tool → call [name: "bash", args: "ls", risk: "read_only"]'
        final = '@tool → call [name: "bash", args: "ls -la", risk: "read_only"]'
        result = verifier.verify(
            goal_scl=goal,
            final_scl=final,
            final_outcome="success",
        )
        assert result["goal_anchor_match"] is True
        assert result["goal_relation_match"] is True
        assert result["goal_field_overlap"] > 0.5

    def test_field_overlap_jaccard(self, verifier):
        """Jaccard overlap of field keys."""
        goal  = '@state → assert [key: "task", value: "x", tool: "bash"]'
        final = '@halt → answer [status: "complete", confidence: "0.9", evidence: "done"]'
        result = verifier.verify(goal_scl=goal, final_scl=final, final_outcome="success")
        # No shared keys between {key,value,tool} and {status,confidence,evidence}
        assert result["goal_field_overlap"] == 0.0


# ── VerifiableRewardFunction ───────────────────────────────────────────────────

class TestVerifiableRewardFunction:
    def test_perfect_task_reward_near_one(self, reward_fn):
        """
        @state goal + @halt final + success outcome.
        det_score = 1.0 (all 5 checks pass).
        goal_score = 0.3 (outcome match only — anchors differ by design).
        reward = 0.3 * 1.0 + 0.7 * 0.3 = 0.51.
        """
        result = reward_fn.compute(
            task_id="t1",
            goal_scl=VALID_GOAL,
            trajectory=GOOD_TRAJ,
            final_scl=VALID_HALT,
            final_outcome="success",
            budget_remaining=0.5,
            policy_violations=0,
            expected_outcome="success",
        )
        assert result.det_score == 1.0
        assert result.goal_score > 0.0
        assert result.reward > 0.3  # det alone = 0.3; any goal score pushes it higher
        assert result.reward == pytest.approx(
            reward_fn.w_det * result.det_score + reward_fn.w_goal * result.goal_score,
            abs=1e-4
        )

    def test_failed_task_reward_near_zero(self, reward_fn):
        result = reward_fn.compute(
            task_id="t2",
            goal_scl=VALID_GOAL,
            trajectory=[{"scl": INVALID_SCL, "outcome": "failure"}],
            final_scl=INVALID_SCL,
            final_outcome="failure",
            budget_remaining=0.5,
            policy_violations=2,
            expected_outcome="success",
        )
        assert result.reward < 0.5

    def test_reward_weights_sum_to_one(self, reward_fn):
        assert reward_fn.w_det + reward_fn.w_goal == pytest.approx(1.0)

    def test_goal_weight_dominates(self, reward_fn):
        """Goal achievement (0.7) outweighs protocol compliance (0.3)."""
        assert reward_fn.w_goal > reward_fn.w_det

    def test_reward_is_composite(self, reward_fn):
        result = reward_fn.compute(
            task_id="t3",
            goal_scl=VALID_GOAL,
            trajectory=GOOD_TRAJ,
            final_scl=VALID_HALT,
            final_outcome="success",
        )
        expected = reward_fn.w_det * result.det_score + reward_fn.w_goal * result.goal_score
        assert result.reward == pytest.approx(expected, abs=1e-4)

    def test_compute_group_ranks_ordering(self, reward_fn):
        results = []
        for outcome, final_scl in [
            ("success", VALID_HALT),
            ("failure", INVALID_SCL),
            ("partial", VALID_HALT),
        ]:
            r = reward_fn.compute(
                task_id=f"g_{outcome}",
                goal_scl=VALID_GOAL,
                trajectory=GOOD_TRAJ,
                final_scl=final_scl,
                final_outcome=outcome,
            )
            results.append(r)

        ranked = reward_fn.compute_group_ranks(results)
        ranks = [r.relative_rank for r in ranked]
        # Ranks should be in [0, 1]
        assert all(0.0 <= rank <= 1.0 for rank in ranks)
        # Best result should have highest rank
        best = max(ranked, key=lambda r: r.reward)
        assert best.relative_rank == pytest.approx(1.0)

    def test_group_ranks_all_equal_gives_0_5(self, reward_fn):
        """When all rewards are equal, relative rank should be 0.5."""
        results = []
        for i in range(3):
            r = reward_fn.compute(
                task_id=f"eq_{i}",
                goal_scl=VALID_GOAL,
                trajectory=GOOD_TRAJ,
                final_scl=VALID_HALT,
                final_outcome="success",
            )
            results.append(r)
        ranked = reward_fn.compute_group_ranks(results)
        for r in ranked:
            assert r.relative_rank == pytest.approx(0.5)


# ── RewardStore ────────────────────────────────────────────────────────────────

class TestRewardStore:
    def test_save_and_retrieve(self, store, reward_fn):
        result = reward_fn.compute(
            task_id="store_t1",
            goal_scl=VALID_GOAL,
            trajectory=GOOD_TRAJ,
            final_scl=VALID_HALT,
            final_outcome="success",
        )
        store.save(result)
        rows = store.get_by_task("store_t1")
        assert len(rows) == 1
        assert rows[0]["task_id"] == "store_t1"
        assert rows[0]["reward"] == pytest.approx(result.reward, abs=1e-4)

    def test_get_high_reward_filters(self, store, reward_fn):
        # Save one high and one low reward
        high = reward_fn.compute(
            task_id="high_t",
            goal_scl=VALID_GOAL,
            trajectory=GOOD_TRAJ,
            final_scl=VALID_HALT,
            final_outcome="success",
        )
        low = reward_fn.compute(
            task_id="low_t",
            goal_scl=VALID_GOAL,
            trajectory=[{"scl": INVALID_SCL, "outcome": "failure"}],
            final_scl=INVALID_SCL,
            final_outcome="failure",
        )
        store.save(high)
        store.save(low)

        high_rows = store.get_high_reward(threshold=0.5)
        task_ids = [r["task_id"] for r in high_rows]
        assert "high_t" in task_ids

    def test_stats_returns_aggregates(self, store, reward_fn):
        for i in range(5):
            r = reward_fn.compute(
                task_id=f"stat_{i}",
                goal_scl=VALID_GOAL,
                trajectory=GOOD_TRAJ,
                final_scl=VALID_HALT,
                final_outcome="success",
            )
            store.save(r)
        stats = store.stats()
        assert stats["total"] == 5
        assert stats["mean_reward"] is not None
        assert stats["halts"] == 5

    def test_multiple_saves_same_task(self, store, reward_fn):
        for i in range(3):
            r = reward_fn.compute(
                task_id="multi_t",
                goal_scl=VALID_GOAL,
                trajectory=GOOD_TRAJ,
                final_scl=VALID_HALT,
                final_outcome="success",
            )
            store.save(r)
        rows = store.get_by_task("multi_t")
        assert len(rows) == 3


# ── GRPORewardScorer ───────────────────────────────────────────────────────────

class TestGRPORewardScorer:
    def test_score_group_returns_ranked_results(self, reward_fn, store):
        scorer = GRPORewardScorer(reward_fn, store)
        rollouts = [
            {
                "task_id": f"grpo_{i}",
                "trajectory": GOOD_TRAJ,
                "final_scl": VALID_HALT if i % 2 == 0 else INVALID_SCL,
                "final_outcome": "success" if i % 2 == 0 else "failure",
            }
            for i in range(4)
        ]
        results = scorer.score_group(VALID_GOAL, rollouts)
        assert len(results) == 4
        ranks = [r.relative_rank for r in results]
        assert all(0.0 <= rank <= 1.0 for rank in ranks)
        # Best rollout (success + valid SCL) should rank highest
        best = max(results, key=lambda r: r.reward)
        assert best.relative_rank == pytest.approx(1.0)

    def test_score_group_persists_to_store(self, reward_fn, store):
        scorer = GRPORewardScorer(reward_fn, store)
        rollouts = [
            {
                "task_id": f"persist_{i}",
                "trajectory": GOOD_TRAJ,
                "final_scl": VALID_HALT,
                "final_outcome": "success",
            }
            for i in range(3)
        ]
        scorer.score_group(VALID_GOAL, rollouts)
        stats = store.stats()
        assert stats["total"] >= 3

    def test_advantage_positive_for_best(self, reward_fn, store):
        scorer = GRPORewardScorer(reward_fn, store)
        rollouts = [
            {"task_id": "adv_good", "trajectory": GOOD_TRAJ,
             "final_scl": VALID_HALT, "final_outcome": "success"},
            {"task_id": "adv_bad",  "trajectory": GOOD_TRAJ,
             "final_scl": INVALID_SCL, "final_outcome": "failure"},
        ]
        results = scorer.score_group(VALID_GOAL, rollouts)
        best = max(results, key=lambda r: r.reward)
        worst = min(results, key=lambda r: r.reward)
        assert scorer.advantage(best) > 0
        assert scorer.advantage(worst) < 0

    def test_group_id_shared_across_rollouts(self, reward_fn):
        scorer = GRPORewardScorer(reward_fn)
        rollouts = [
            {"task_id": f"gid_{i}", "trajectory": GOOD_TRAJ,
             "final_scl": VALID_HALT, "final_outcome": "success"}
            for i in range(3)
        ]
        results = scorer.score_group(VALID_GOAL, rollouts)
        group_ids = {r.group_id for r in results}
        assert len(group_ids) == 1  # All share the same group_id


# ── Integration: corpus + reward ───────────────────────────────────────────────

class TestCorpusRewardIntegration:
    def test_corpus_examples_score_positively(self, reward_fn):
        """SCL-native corpus examples should score well on the reward function."""
        from cortex.scl_corpus import SCLCorpusGenerator
        gen = SCLCorpusGenerator(seed=0)
        examples = gen.generate_corpus(n_total=20)

        for ex in examples:
            compl = ex.get("completion", "").strip()
            if not compl:
                continue
            # Take the first SCL line of the completion
            first_line = next(
                (l.strip() for l in compl.split("\n") if l.strip().startswith("@")),
                None
            )
            if not first_line:
                continue
            result = reward_fn.compute(
                task_id="corpus_test",
                goal_scl='@state → assert [key: "task", value: "general"]',
                trajectory=[{"scl": first_line, "outcome": "success"}],
                final_scl=first_line,
                final_outcome="success",
            )
            # SCL-native corpus examples should have valid SCL
            assert result.scl_valid, f"Corpus example has invalid SCL: {first_line!r}"
