"""
store.py — Cortex Persistent Trajectory Store

SQLite-backed append-only log of every runtime step. This is the single
source of truth for the continuous learning flywheel:

    runtime → store.log_step()
            → compactor.compact()
            → exporter.export_sft()
            → lora_finetune.py
            → better model
            → better trajectories
            → repeat

Schema
------
trajectories
    id          INTEGER PK
    task_id     TEXT        — unique task run identifier
    step        INTEGER     — step index within the task (0-based)
    phase       TEXT        — diagnose | plan | act | verify | halt
    goal        TEXT        — task goal string
    prompt      TEXT        — full SFT prompt fed to the model
    completion  TEXT        — model output (raw)
    scl_valid   INTEGER     — 1 if completion parsed as valid SCL
    policy_ok   INTEGER     — 1 if policy allowed the action
    verified    INTEGER     — 1 if verifier post-check passed
    outcome     TEXT        — success | denied | error | timeout | pending
    reward      REAL        — scalar reward (-1.0 to 1.0)
    units_used  REAL        — budget units consumed by this step
    tool_name   TEXT        — tool called (nullable)
    risk_tier   TEXT        — read_only | low | medium | high | critical
    ts          TEXT        — ISO-8601 timestamp
    meta        TEXT        — JSON blob for arbitrary extra fields

tasks
    task_id     TEXT PK
    goal        TEXT
    status      TEXT        — running | success | failed | timeout
    total_steps INTEGER
    total_units REAL
    started_at  TEXT
    finished_at TEXT
    model_ver   TEXT        — model checkpoint that ran this task

compaction_log
    id          INTEGER PK
    run_at      TEXT
    strategy    TEXT        — full | incremental | quality_filter
    rows_in     INTEGER
    rows_out    INTEGER
    sft_train   TEXT        — path to exported sft_train.jsonl
    sft_val     TEXT        — path to exported sft_val.jsonl
    notes       TEXT
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

_DEFAULT_DB = Path("data/cortex.db")

# Thread-local connection cache so each thread gets its own connection
_local = threading.local()


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class TrajectoryStore:
    """Thread-safe SQLite trajectory store with WAL mode for concurrent writes."""

    def __init__(self, db_path: Path = _DEFAULT_DB):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Initialise schema on first open
        with self._conn() as conn:
            self._create_schema(conn)

    # ── Connection management ─────────────────────────────────────────────────

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        """Return a per-thread connection with WAL mode enabled."""
        if not hasattr(_local, "conn") or _local.db_path != str(self.db_path):
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.row_factory = sqlite3.Row
            _local.conn = conn
            _local.db_path = str(self.db_path)
        try:
            yield _local.conn
        except Exception:
            _local.conn.rollback()
            raise

    # ── Schema ────────────────────────────────────────────────────────────────

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS trajectories (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id     TEXT    NOT NULL,
                step        INTEGER NOT NULL,
                phase       TEXT,
                goal        TEXT,
                prompt      TEXT,
                completion  TEXT,
                scl_valid   INTEGER DEFAULT 0,
                policy_ok   INTEGER DEFAULT 0,
                verified    INTEGER DEFAULT 0,
                outcome     TEXT    DEFAULT 'pending',
                reward      REAL    DEFAULT 0.0,
                units_used  REAL    DEFAULT 0.0,
                tool_name   TEXT,
                risk_tier   TEXT,
                ts          TEXT,
                meta        TEXT    DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_traj_task   ON trajectories(task_id);
            CREATE INDEX IF NOT EXISTS idx_traj_outcome ON trajectories(outcome);
            CREATE INDEX IF NOT EXISTS idx_traj_reward  ON trajectories(reward);
            CREATE INDEX IF NOT EXISTS idx_traj_ts      ON trajectories(ts);

            CREATE TABLE IF NOT EXISTS tasks (
                task_id     TEXT PRIMARY KEY,
                goal        TEXT,
                status      TEXT    DEFAULT 'running',
                total_steps INTEGER DEFAULT 0,
                total_units REAL    DEFAULT 0.0,
                started_at  TEXT,
                finished_at TEXT,
                model_ver   TEXT
            );

            CREATE TABLE IF NOT EXISTS compaction_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at      TEXT,
                strategy    TEXT,
                rows_in     INTEGER,
                rows_out    INTEGER,
                sft_train   TEXT,
                sft_val     TEXT,
                notes       TEXT
            );
        """)
        conn.commit()

    # ── Write API ─────────────────────────────────────────────────────────────

    def start_task(
        self,
        task_id: str,
        goal: str,
        model_ver: str = "unknown",
    ) -> None:
        """Register a new task run."""
        with self._conn() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO tasks
                   (task_id, goal, status, started_at, model_ver)
                   VALUES (?, ?, 'running', ?, ?)""",
                (task_id, goal, _utcnow(), model_ver),
            )
            conn.commit()

    def log_step(
        self,
        task_id: str,
        step: int,
        prompt: str,
        completion: str,
        *,
        phase: str = "act",
        goal: str = "",
        scl_valid: bool = False,
        policy_ok: bool = False,
        verified: bool = False,
        outcome: str = "pending",
        reward: float = 0.0,
        units_used: float = 0.0,
        tool_name: Optional[str] = None,
        risk_tier: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Append one step to the trajectory log. Returns the row id."""
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO trajectories
                   (task_id, step, phase, goal, prompt, completion,
                    scl_valid, policy_ok, verified, outcome, reward,
                    units_used, tool_name, risk_tier, ts, meta)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    task_id, step, phase, goal, prompt, completion,
                    int(scl_valid), int(policy_ok), int(verified),
                    outcome, reward, units_used, tool_name, risk_tier,
                    _utcnow(), json.dumps(meta or {}),
                ),
            )
            conn.commit()
            return cur.lastrowid

    def finish_task(
        self,
        task_id: str,
        status: str,
        total_steps: int,
        total_units: float,
    ) -> None:
        """Mark a task as finished and record final stats."""
        with self._conn() as conn:
            conn.execute(
                """UPDATE tasks SET
                   status=?, total_steps=?, total_units=?, finished_at=?
                   WHERE task_id=?""",
                (status, total_steps, total_units, _utcnow(), task_id),
            )
            conn.commit()

    def update_step_outcome(
        self,
        row_id: int,
        outcome: str,
        reward: float,
        verified: bool = False,
    ) -> None:
        """Retroactively update outcome/reward after verification completes."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE trajectories SET outcome=?, reward=?, verified=? WHERE id=?",
                (outcome, reward, int(verified), row_id),
            )
            conn.commit()

    # ── Read API ──────────────────────────────────────────────────────────────

    def get_task_steps(self, task_id: str) -> List[sqlite3.Row]:
        """Return all steps for a task in order."""
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM trajectories WHERE task_id=? ORDER BY step",
                (task_id,),
            ).fetchall()

    def query(
        self,
        *,
        min_reward: float = -999.0,
        outcome: Optional[str] = None,
        scl_valid: Optional[bool] = None,
        policy_ok: Optional[bool] = None,
        verified: Optional[bool] = None,
        limit: int = 10_000,
        offset: int = 0,
    ) -> List[sqlite3.Row]:
        """Flexible query for compaction and export pipelines."""
        clauses = ["1=1"]
        params: List[Any] = []

        if min_reward > -999.0:
            clauses.append("reward >= ?"); params.append(min_reward)
        if outcome is not None:
            clauses.append("outcome = ?"); params.append(outcome)
        if scl_valid is not None:
            clauses.append("scl_valid = ?"); params.append(int(scl_valid))
        if policy_ok is not None:
            clauses.append("policy_ok = ?"); params.append(int(policy_ok))
        if verified is not None:
            clauses.append("verified = ?"); params.append(int(verified))

        sql = (
            f"SELECT * FROM trajectories WHERE {' AND '.join(clauses)}"
            f" ORDER BY reward DESC, ts DESC LIMIT ? OFFSET ?"
        )
        params += [limit, offset]

        with self._conn() as conn:
            return conn.execute(sql, params).fetchall()

    def stats(self) -> Dict[str, Any]:
        """Return a summary dict for monitoring."""
        with self._conn() as conn:
            total     = conn.execute("SELECT COUNT(*) FROM trajectories").fetchone()[0]
            success   = conn.execute("SELECT COUNT(*) FROM trajectories WHERE outcome='success'").fetchone()[0]
            denied    = conn.execute("SELECT COUNT(*) FROM trajectories WHERE outcome='denied'").fetchone()[0]
            scl_ok    = conn.execute("SELECT COUNT(*) FROM trajectories WHERE scl_valid=1").fetchone()[0]
            avg_rew   = conn.execute("SELECT AVG(reward) FROM trajectories").fetchone()[0] or 0.0
            tasks_done = conn.execute("SELECT COUNT(*) FROM tasks WHERE status='success'").fetchone()[0]
            tasks_fail = conn.execute("SELECT COUNT(*) FROM tasks WHERE status='failed'").fetchone()[0]
            last_compact = conn.execute(
                "SELECT run_at FROM compaction_log ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return {
            "total_steps": total,
            "success_steps": success,
            "denied_steps": denied,
            "scl_valid_steps": scl_ok,
            "avg_reward": round(avg_rew, 4),
            "tasks_success": tasks_done,
            "tasks_failed": tasks_fail,
            "last_compaction": last_compact[0] if last_compact else None,
        }

    def log_compaction(
        self,
        strategy: str,
        rows_in: int,
        rows_out: int,
        sft_train: str,
        sft_val: str,
        notes: str = "",
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO compaction_log
                   (run_at, strategy, rows_in, rows_out, sft_train, sft_val, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (_utcnow(), strategy, rows_in, rows_out, sft_train, sft_val, notes),
            )
            conn.commit()
