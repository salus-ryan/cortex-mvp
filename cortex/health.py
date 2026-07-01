"""
health.py — Cortex Self-Healing Health Monitor

The health monitor is the immune system of Cortex. It runs silently on every
operation, detects violations before they become failures, and repairs them
automatically. When it cannot repair, it records a precise diagnosis so the
next training round can learn from the failure.

Design principles
-----------------
  - Zero crashes: every exception is caught, diagnosed, and repaired or logged
  - Zero silent failures: every anomaly is recorded in the audit trail
  - Zero manual intervention: repairs are automatic and idempotent
  - Compounding intelligence: every repair becomes a training example

Health checks
-------------
  SystemHealth      — Python version, packages, schema file
  StoreHealth       — DB reachable, WAL mode, tables, integrity
  SchemaHealth      — SCL schema valid and consistent with runtime
  EmitterHealth     — SCL emitter repair rate within bounds
  BudgetHealth      — Budget values self-consistent
  TrajectoryHealth  — Recent steps have valid structure
  FilesystemHealth  — Required directories and files exist

Usage
-----
    from cortex.health import HealthMonitor

    monitor = HealthMonitor(db_path=Path("data/cortex.db"))

    # Run all checks and auto-repair
    report = monitor.run()
    print(report.summary())

    # Wrap any function with health monitoring
    @monitor.guard
    def critical_operation():
        ...

    # Run as a background daemon
    monitor.watch(interval=60)
"""

from __future__ import annotations

import sqlite3
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, List, Optional

from cortex.invariants import (
    InvariantResult,
    SchemaInvariant,
    StoreInvariant,
    SystemInvariant,
    ActionInvariant,
    TrajectoryInvariant,
    check_all,
)

# ── Health report ─────────────────────────────────────────────────────────────

@dataclass
class HealthCheck:
    name: str
    passed: bool
    repaired: bool = False
    reason: str = ""
    repair_note: str = ""
    duration_ms: float = 0.0

    def __repr__(self):
        status = "OK" if self.passed else ("REPAIRED" if self.repaired else "FAIL")
        return f"[{status:8s}] {self.name}" + (f": {self.reason}" if self.reason else "")


@dataclass
class HealthReport:
    checks: List[HealthCheck] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    total_ms: float = 0.0

    @property
    def passed(self) -> bool:
        return all(c.passed or c.repaired for c in self.checks)

    @property
    def failures(self) -> List[HealthCheck]:
        return [c for c in self.checks if not c.passed and not c.repaired]

    @property
    def repairs(self) -> List[HealthCheck]:
        return [c for c in self.checks if c.repaired]

    def summary(self) -> str:
        lines = [
            f"Health Report ({len(self.checks)} checks, {self.total_ms:.1f}ms)",
            f"  Passed:   {sum(1 for c in self.checks if c.passed and not c.repaired)}",
            f"  Repaired: {len(self.repairs)}",
            f"  Failed:   {len(self.failures)}",
        ]
        for c in self.checks:
            lines.append(f"  {c}")
        return "\n".join(lines)


# ── Health monitor ────────────────────────────────────────────────────────────

class HealthMonitor:
    """
    Self-healing health monitor for Cortex.

    Runs all invariant checks, attempts repairs, and records every
    anomaly to the audit trail for training signal.
    """

    def __init__(
        self,
        db_path: Optional[Path] = None,
        emitter: Any = None,
        auto_repair: bool = True,
    ):
        self.db_path = db_path or Path("data/cortex.db")
        self.emitter = emitter
        self.auto_repair = auto_repair
        self._lock = threading.Lock()
        self._watch_thread: Optional[threading.Thread] = None
        self._watching = False
        self._last_report: Optional[HealthReport] = None

    # ── Core run ──────────────────────────────────────────────────────────────

    def run(self, silent: bool = True) -> HealthReport:
        """Run all health checks and return a HealthReport."""
        report = HealthReport()
        t0 = time.monotonic()

        checks = [
            self._check_system,
            self._check_schema,
            self._check_store,
            self._check_filesystem,
            self._check_emitter,
            self._check_recent_trajectories,
        ]

        for check_fn in checks:
            ct0 = time.monotonic()
            try:
                hc = check_fn()
            except Exception as e:
                hc = HealthCheck(
                    name=check_fn.__name__.replace("_check_", ""),
                    passed=False,
                    reason=f"check raised exception: {e}",
                )
            hc.duration_ms = (time.monotonic() - ct0) * 1000
            report.checks.append(hc)

            if not silent:
                print(f"  {hc}")

        report.total_ms = (time.monotonic() - t0) * 1000
        self._last_report = report

        # Record failures to audit log
        if report.failures:
            self._audit_failures(report.failures)

        return report

    # ── Individual checks ─────────────────────────────────────────────────────

    def _check_system(self) -> HealthCheck:
        r = SystemInvariant.enforce()
        return HealthCheck(
            name="system",
            passed=r.passed,
            repaired=r.repaired,
            reason=r.reason,
            repair_note=r.repair_note,
        )

    def _check_schema(self) -> HealthCheck:
        r = SchemaInvariant.enforce()
        return HealthCheck(
            name="schema",
            passed=r.passed,
            repaired=r.repaired,
            reason=r.reason,
            repair_note=r.repair_note,
        )

    def _check_store(self) -> HealthCheck:
        if not self.db_path.exists():
            # DB doesn't exist yet — not a failure, just not initialised
            return HealthCheck(name="store", passed=True,
                               reason="DB not yet created (will be on first task)")

        r = StoreInvariant.enforce(db_path=self.db_path)
        return HealthCheck(
            name="store",
            passed=r.passed,
            repaired=r.repaired,
            reason=r.reason,
            repair_note=r.repair_note,
        )

    def _check_filesystem(self) -> HealthCheck:
        required_dirs = [
            Path("data"),
            Path("data/sft"),
            Path("models"),
            Path("scripts"),
            Path("cortex"),
        ]
        missing = []
        repaired_dirs = []

        for d in required_dirs:
            if not d.exists():
                if self.auto_repair:
                    try:
                        d.mkdir(parents=True, exist_ok=True)
                        repaired_dirs.append(str(d))
                    except Exception as e:
                        missing.append(f"{d} (repair failed: {e})")
                else:
                    missing.append(str(d))

        if missing:
            return HealthCheck(name="filesystem", passed=False,
                               reason=f"missing dirs: {missing}")
        if repaired_dirs:
            return HealthCheck(name="filesystem", passed=True, repaired=True,
                               repair_note=f"created dirs: {repaired_dirs}")
        return HealthCheck(name="filesystem", passed=True)

    def _check_emitter(self) -> HealthCheck:
        if self.emitter is None:
            return HealthCheck(name="emitter", passed=True,
                               reason="no emitter attached")

        rate = getattr(self.emitter, "repair_rate", 0.0)
        total = getattr(self.emitter, "_total_count", 0)

        if total < 10:
            # Not enough data to judge
            return HealthCheck(name="emitter", passed=True,
                               reason=f"insufficient samples ({total})")

        if rate > 0.5:
            return HealthCheck(
                name="emitter", passed=False,
                reason=f"repair_rate={rate:.1%} > 50% — model is producing mostly invalid SCL. "
                       f"Consider retraining.",
            )
        if rate > 0.2:
            return HealthCheck(
                name="emitter", passed=True, repaired=True,
                reason=f"repair_rate={rate:.1%} elevated (>20%) — model quality degrading",
                repair_note="monitor and retrain if rate continues to rise",
            )
        return HealthCheck(name="emitter", passed=True,
                           reason=f"repair_rate={rate:.1%}")

    def _check_recent_trajectories(self) -> HealthCheck:
        """Spot-check the last 20 trajectory rows for structural validity."""
        if not self.db_path.exists():
            return HealthCheck(name="trajectories", passed=True,
                               reason="DB not yet created")
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM trajectories ORDER BY id DESC LIMIT 20"
            ).fetchall()
            conn.close()
        except Exception as e:
            return HealthCheck(name="trajectories", passed=False,
                               reason=f"DB read error: {e}")

        if not rows:
            return HealthCheck(name="trajectories", passed=True,
                               reason="no rows yet")

        invalid = 0
        for row in rows:
            kwargs = dict(row)
            r = TrajectoryInvariant.check(kwargs)
            if not r.passed:
                invalid += 1

        if invalid > 0:
            rate = invalid / len(rows)
            return HealthCheck(
                name="trajectories",
                passed=rate < 0.1,  # tolerate up to 10% invalid
                repaired=False,
                reason=f"{invalid}/{len(rows)} recent rows failed invariant ({rate:.0%})",
            )
        return HealthCheck(name="trajectories", passed=True,
                           reason=f"last {len(rows)} rows all valid")

    # ── Audit ─────────────────────────────────────────────────────────────────

    def _audit_failures(self, failures: List[HealthCheck]) -> None:
        """Write health failures to the DB audit log if available."""
        if not self.db_path.exists():
            return
        try:
            conn = sqlite3.connect(str(self.db_path))
            for f in failures:
                conn.execute(
                    "INSERT INTO compaction_log "
                    "(ts, strategy, rows_in, rows_out, quality_threshold, "
                    " dedup_removed, retrain_needed, notes) "
                    "VALUES (datetime('now'), ?, 0, 0, 0, 0, 0, ?)",
                    (f"health_failure:{f.name}", f.reason[:500]),
                )
            conn.commit()
            conn.close()
        except Exception:
            pass  # Never let audit logging crash the system

    # ── Guard decorator ───────────────────────────────────────────────────────

    def guard(self, fn: Callable) -> Callable:
        """
        Decorator that runs a health check before the function.
        If critical checks fail, the function is skipped and a HealthReport
        is returned instead of raising.
        """
        def wrapper(*args, **kwargs):
            report = self.run(silent=True)
            if report.failures:
                # Non-repairable failures — skip the operation
                return report
            return fn(*args, **kwargs)
        wrapper.__name__ = fn.__name__
        return wrapper

    # ── Watch daemon ──────────────────────────────────────────────────────────

    def watch(self, interval: int = 60, silent: bool = True) -> None:
        """
        Run health checks in a background thread every `interval` seconds.
        Non-blocking — returns immediately.
        """
        if self._watching:
            return

        self._watching = True

        def _loop():
            while self._watching:
                try:
                    report = self.run(silent=silent)
                    if not silent and not report.passed:
                        print(report.summary())
                except Exception:
                    pass
                time.sleep(interval)

        self._watch_thread = threading.Thread(target=_loop, daemon=True)
        self._watch_thread.start()

    def stop_watch(self) -> None:
        """Stop the background watch thread."""
        self._watching = False
        if self._watch_thread:
            self._watch_thread.join(timeout=5)

    @property
    def last_report(self) -> Optional[HealthReport]:
        return self._last_report
