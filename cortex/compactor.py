"""
compactor.py — Cortex Intelligent Compaction Pipeline

Turns the raw append-only trajectory log in SQLite into a clean, deduplicated,
quality-scored SFT dataset ready for LoRA fine-tuning.

Compaction strategies
---------------------
full
    Re-score and re-export every row in the DB. Use after a major policy change
    or when the reward function itself has changed.

incremental
    Only process rows added since the last compaction run. Fast; use after each
    task batch.

quality_filter
    Re-run quality scoring on all rows and drop anything that has fallen below
    the current threshold. Use to prune stale or low-quality data.

Quality scoring
---------------
Each trajectory step is scored on five axes (all 0–1, equal weight):

    scl_validity    — did the completion parse as valid SCL?
    policy_pass     — did the policy allow the action?
    verifier_pass   — did the verifier post-check pass?
    outcome_score   — success=1.0, pending=0.5, denied/error/timeout=0.0
    reward_norm     — (reward + 1) / 2  (maps [-1,1] → [0,1])

Final quality score = mean of the five axes.
Default threshold: 0.6 (configurable).

Deduplication
-------------
Exact-duplicate prompts are collapsed to the highest-quality version.
Near-duplicate detection uses a simple shingling hash on the prompt text
(4-gram shingles, Jaccard similarity > 0.85 → duplicate).

Recursive compaction
--------------------
After each compaction pass, the compactor checks whether the dataset has grown
by more than `growth_factor` (default 2×) since the last LoRA checkpoint. If
so, it emits a retrain signal (writes a sentinel file `data/.retrain_needed`)
that the continuous learning loop polls.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from cortex.store import TrajectoryStore

_DEFAULT_QUALITY_THRESHOLD = 0.6
_DEFAULT_DEDUP_JACCARD = 0.85
_SHINGLE_N = 4
_RETRAIN_GROWTH_FACTOR = 2.0
_RETRAIN_SENTINEL = Path("data/.retrain_needed")


# ── Quality scoring ───────────────────────────────────────────────────────────

def _outcome_score(outcome: str) -> float:
    return {"success": 1.0, "pending": 0.5}.get(outcome, 0.0)


def quality_score(row: Any) -> float:
    """Compute a [0,1] quality score for a single trajectory row."""
    scl   = float(row["scl_valid"])
    pol   = float(row["policy_ok"])
    ver   = float(row["verified"])
    out   = _outcome_score(row["outcome"])
    rew   = (float(row["reward"]) + 1.0) / 2.0  # [-1,1] → [0,1]
    return (scl + pol + ver + out + rew) / 5.0


# ── Deduplication ─────────────────────────────────────────────────────────────

def _shingles(text: str, n: int = _SHINGLE_N) -> Set[str]:
    tokens = re.findall(r"\w+", text.lower())
    return {" ".join(tokens[i : i + n]) for i in range(max(1, len(tokens) - n + 1))}


def _jaccard(a: Set[str], b: Set[str]) -> float:
    if not a and not b:
        return 1.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode()).hexdigest()


def deduplicate(
    rows: List[Any],
    jaccard_threshold: float = _DEFAULT_DEDUP_JACCARD,
) -> List[Any]:
    """
    Remove duplicate rows.

    1. Exact duplicates (same SHA-256 of prompt) → keep highest quality.
    2. Near-duplicates (Jaccard > threshold) → keep highest quality.

    Returns deduplicated list sorted by quality descending.
    """
    # Score everything first
    scored = [(quality_score(r), r) for r in rows]
    scored.sort(key=lambda x: x[0], reverse=True)

    # Exact dedup: keep first (highest quality) occurrence of each prompt hash
    seen_hashes: Set[str] = set()
    after_exact: List[Tuple[float, Any]] = []
    for score, row in scored:
        h = _prompt_hash(row["prompt"] or "")
        if h not in seen_hashes:
            seen_hashes.add(h)
            after_exact.append((score, row))

    # Near-dedup: greedy — keep a row only if it's not too similar to any
    # already-kept row (compare against the last 200 kept shingle sets)
    kept: List[Any] = []
    kept_shingles: List[Set[str]] = []
    window = 200

    for score, row in after_exact:
        sh = _shingles(row["prompt"] or "")
        is_dup = any(
            _jaccard(sh, ks) >= jaccard_threshold
            for ks in kept_shingles[-window:]
        )
        if not is_dup:
            kept.append(row)
            kept_shingles.append(sh)

    return kept


# ── SFT formatting ────────────────────────────────────────────────────────────

def _to_sft_pair(row: Any) -> Dict[str, str]:
    return {
        "prompt":     row["prompt"] or "",
        "completion": row["completion"] or "",
        "quality":    round(quality_score(row), 4),
        "task_id":    row["task_id"],
        "outcome":    row["outcome"],
        "reward":     row["reward"],
    }


# ── Compactor ─────────────────────────────────────────────────────────────────

class Compactor:
    """
    Intelligent, recursive compaction pipeline.

    Usage::

        store = TrajectoryStore()
        compactor = Compactor(store)
        result = compactor.compact(strategy="incremental")
        print(result)
    """

    def __init__(
        self,
        store: TrajectoryStore,
        quality_threshold: float = _DEFAULT_QUALITY_THRESHOLD,
        jaccard_threshold: float = _DEFAULT_DEDUP_JACCARD,
        val_fraction: float = 0.1,
        output_dir: Path = Path("data/sft"),
        retrain_growth_factor: float = _RETRAIN_GROWTH_FACTOR,
    ):
        self.store = store
        self.quality_threshold = quality_threshold
        self.jaccard_threshold = jaccard_threshold
        self.val_fraction = val_fraction
        self.output_dir = Path(output_dir)
        self.retrain_growth_factor = retrain_growth_factor

    # ── Public entry point ────────────────────────────────────────────────────

    def compact(
        self,
        strategy: str = "incremental",
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Run a compaction pass.

        Returns a result dict with keys:
            strategy, rows_in, rows_out, train_path, val_path,
            quality_threshold, retrain_needed
        """
        rows = self._fetch(strategy)
        rows_in = len(rows)

        # Quality filter
        passing = [r for r in rows if quality_score(r) >= self.quality_threshold]

        # Deduplication
        clean = deduplicate(passing, self.jaccard_threshold)
        rows_out = len(clean)

        if dry_run:
            return {
                "strategy": strategy,
                "rows_in": rows_in,
                "rows_out": rows_out,
                "dry_run": True,
                "quality_threshold": self.quality_threshold,
            }

        # Split train/val
        val_n = max(1, int(rows_out * self.val_fraction))
        val_rows   = clean[:val_n]
        train_rows = clean[val_n:]

        # Export
        self.output_dir.mkdir(parents=True, exist_ok=True)
        train_path = self.output_dir / "sft_train.jsonl"
        val_path   = self.output_dir / "sft_val.jsonl"
        _write_jsonl(train_path, [_to_sft_pair(r) for r in train_rows])
        _write_jsonl(val_path,   [_to_sft_pair(r) for r in val_rows])

        # Log to DB
        self.store.log_compaction(
            strategy=strategy,
            rows_in=rows_in,
            rows_out=rows_out,
            sft_train=str(train_path),
            sft_val=str(val_path),
            notes=f"quality>={self.quality_threshold}, jaccard<{self.jaccard_threshold}",
        )

        # Recursive retrain signal
        retrain_needed = self._check_retrain(rows_out)
        if retrain_needed:
            _RETRAIN_SENTINEL.parent.mkdir(parents=True, exist_ok=True)
            _RETRAIN_SENTINEL.write_text(
                json.dumps({
                    "rows_out": rows_out,
                    "train_path": str(train_path),
                    "val_path": str(val_path),
                })
            )

        return {
            "strategy": strategy,
            "rows_in": rows_in,
            "rows_out": rows_out,
            "train_path": str(train_path),
            "val_path": str(val_path),
            "quality_threshold": self.quality_threshold,
            "retrain_needed": retrain_needed,
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _fetch(self, strategy: str) -> List[Any]:
        """Fetch rows from the store according to strategy."""
        if strategy == "full":
            return self.store.query(limit=500_000)
        elif strategy == "quality_filter":
            # Re-score everything; quality filter happens in compact()
            return self.store.query(limit=500_000)
        else:
            # incremental: rows since last compaction
            last_ts = self._last_compaction_ts()
            if last_ts is None:
                return self.store.query(limit=500_000)
            with self.store._conn() as conn:
                return conn.execute(
                    "SELECT * FROM trajectories WHERE ts > ? ORDER BY reward DESC",
                    (last_ts,),
                ).fetchall()

    def _last_compaction_ts(self) -> Optional[str]:
        with self.store._conn() as conn:
            row = conn.execute(
                "SELECT run_at FROM compaction_log ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return row[0] if row else None

    def _check_retrain(self, current_rows: int) -> bool:
        """Signal retrain if dataset has grown by retrain_growth_factor."""
        with self.store._conn() as conn:
            prev = conn.execute(
                "SELECT rows_out FROM compaction_log ORDER BY id DESC LIMIT 1 OFFSET 1"
            ).fetchone()
        if prev is None:
            return current_rows >= 100  # first time: retrain when we have enough data
        return current_rows >= prev[0] * self.retrain_growth_factor

    # ── Recursive compaction ──────────────────────────────────────────────────

    def compact_recursive(
        self,
        max_passes: int = 5,
        strategy: str = "incremental",
    ) -> List[Dict[str, Any]]:
        """
        Run compaction passes until the dataset stabilises (rows_out stops
        shrinking by more than 5%) or max_passes is reached.

        This implements the 'intelligent and recursive compaction' requirement:
        each pass can surface new near-duplicates that only become visible
        after lower-quality rows are removed.
        """
        results = []
        prev_out = None

        for i in range(max_passes):
            result = self.compact(strategy=strategy if i == 0 else "quality_filter")
            results.append(result)
            cur_out = result["rows_out"]

            if prev_out is not None:
                shrink = (prev_out - cur_out) / max(prev_out, 1)
                if shrink < 0.05:
                    # Stable — no meaningful change
                    break

            prev_out = cur_out

        return results


# ── I/O helpers ───────────────────────────────────────────────────────────────

def _write_jsonl(path: Path, records: List[Dict]) -> None:
    with open(path, "w") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")
