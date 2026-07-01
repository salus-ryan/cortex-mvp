#!/usr/bin/env python3
"""
data_cli.py — Cortex Data Management CLI

Single entry point for inspecting, compacting, and managing the persistent
trajectory store.

Commands
--------
    stats           Print store statistics
    compact         Run a compaction pass
    export          Export SFT dataset without compacting
    retrain         Force a retrain from current SFT data
    watch           Start the continuous learning daemon
    tail            Tail the last N trajectory steps
    tasks           List recent tasks
    schema          Print the DB schema

Examples
--------
    python3 scripts/data_cli.py stats
    python3 scripts/data_cli.py compact --strategy incremental
    python3 scripts/data_cli.py compact --strategy full --recursive
    python3 scripts/data_cli.py retrain --epochs 3
    python3 scripts/data_cli.py watch --interval 300
    python3 scripts/data_cli.py tail --n 20
    python3 scripts/data_cli.py tasks --limit 10
"""

import argparse
import json
import sys
from pathlib import Path

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

from cortex.store import TrajectoryStore
from cortex.compactor import Compactor
from cortex.learner import run_once, watch as _watch, retrain as _retrain

_DB_PATH  = Path("data/cortex.db")
_SFT_DIR  = Path("data/sft")


def cmd_stats(store: TrajectoryStore, args) -> None:
    s = store.stats()
    print("\nCortex Trajectory Store — Statistics")
    print("─" * 40)
    for k, v in s.items():
        print(f"  {k:<25} {v}")
    print()


def cmd_compact(store: TrajectoryStore, args) -> None:
    c = Compactor(store, output_dir=_SFT_DIR,
                  quality_threshold=args.quality_threshold)
    if args.recursive:
        results = c.compact_recursive(strategy=args.strategy)
        for i, r in enumerate(results):
            print(f"\nPass {i+1}: {r}")
    else:
        result = c.compact(strategy=args.strategy, dry_run=args.dry_run)
        print(json.dumps(result, indent=2, default=str))


def cmd_export(store: TrajectoryStore, args) -> None:
    """Export current store to SFT JSONL without running quality filter."""
    rows = store.query(limit=500_000)
    from cortex.compactor import _to_sft_pair, _write_jsonl
    pairs = [_to_sft_pair(r) for r in rows]
    val_n = max(1, int(len(pairs) * 0.1))
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    _write_jsonl(out / "sft_train.jsonl", pairs[val_n:])
    _write_jsonl(out / "sft_val.jsonl",   pairs[:val_n])
    print(f"Exported {len(pairs[val_n:])} train + {val_n} val → {out}/")


def cmd_retrain(store: TrajectoryStore, args) -> None:
    train = str(_SFT_DIR / "sft_train.jsonl")
    val   = str(_SFT_DIR / "sft_val.jsonl")
    if not Path(train).exists():
        print("No SFT data found. Run `compact` first.")
        sys.exit(1)
    checkpoint = _retrain(
        train_path=train,
        val_path=val,
        model_base=args.model,
        epochs=args.epochs,
        batch_size=args.batch_size,
    )
    print(f"Checkpoint saved to: {checkpoint}")


def cmd_watch(store: TrajectoryStore, args) -> None:
    _watch(store, interval=args.interval, strategy=args.strategy,
           epochs=args.epochs, batch_size=args.batch_size, model_base=args.model)


def cmd_tail(store: TrajectoryStore, args) -> None:
    rows = store.query(limit=args.n)
    print(f"\nLast {len(rows)} trajectory steps (highest reward first):\n")
    print(f"{'id':>6}  {'task_id':<24}  {'step':>4}  {'outcome':<10}  "
          f"{'reward':>7}  {'tool':<20}  {'ts'}")
    print("─" * 90)
    for r in rows:
        print(f"{r['id']:>6}  {(r['task_id'] or '')[:24]:<24}  {r['step']:>4}  "
              f"{(r['outcome'] or ''):<10}  {r['reward']:>7.3f}  "
              f"{(r['tool_name'] or '-')[:20]:<20}  {r['ts']}")
    print()


def cmd_tasks(store: TrajectoryStore, args) -> None:
    with store._conn() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks ORDER BY started_at DESC LIMIT ?",
            (args.limit,)
        ).fetchall()
    print(f"\n{'task_id':<36}  {'status':<10}  {'steps':>5}  {'units':>8}  "
          f"{'started_at'}")
    print("─" * 80)
    for r in rows:
        print(f"{(r['task_id'] or '')[:36]:<36}  {(r['status'] or ''):<10}  "
              f"{r['total_steps']:>5}  {r['total_units']:>8.2f}  {r['started_at']}")
    print()


def cmd_schema(store: TrajectoryStore, args) -> None:
    with store._conn() as conn:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        for t in tables:
            name = t[0]
            print(f"\n-- {name}")
            rows = conn.execute(f"PRAGMA table_info({name})").fetchall()
            for r in rows:
                print(f"   {r['name']:<25} {r['type']}")
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Cortex Data Management CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--db", default=str(_DB_PATH), help="SQLite DB path")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # stats
    sub.add_parser("stats", help="Print store statistics")

    # compact
    p_compact = sub.add_parser("compact", help="Run a compaction pass")
    p_compact.add_argument("--strategy", default="incremental",
                           choices=["incremental", "full", "quality_filter"])
    p_compact.add_argument("--quality-threshold", type=float, default=0.6,
                           dest="quality_threshold")
    p_compact.add_argument("--recursive", action="store_true")
    p_compact.add_argument("--dry-run",   action="store_true", dest="dry_run")

    # export
    p_export = sub.add_parser("export", help="Export SFT dataset")
    p_export.add_argument("--output", default=str(_SFT_DIR))

    # retrain
    p_retrain = sub.add_parser("retrain", help="Force a retrain")
    p_retrain.add_argument("--model",      default=None)
    p_retrain.add_argument("--epochs",     type=int, default=1)
    p_retrain.add_argument("--batch-size", type=int, default=4, dest="batch_size")

    # watch
    p_watch = sub.add_parser("watch", help="Start continuous learning daemon")
    p_watch.add_argument("--interval",   type=int, default=300)
    p_watch.add_argument("--strategy",   default="incremental",
                         choices=["incremental", "full", "quality_filter"])
    p_watch.add_argument("--epochs",     type=int, default=1)
    p_watch.add_argument("--batch-size", type=int, default=4, dest="batch_size")
    p_watch.add_argument("--model",      default=None)

    # tail
    p_tail = sub.add_parser("tail", help="Tail recent trajectory steps")
    p_tail.add_argument("--n", type=int, default=20)

    # tasks
    p_tasks = sub.add_parser("tasks", help="List recent tasks")
    p_tasks.add_argument("--limit", type=int, default=20)

    # schema
    sub.add_parser("schema", help="Print DB schema")

    args = parser.parse_args()
    store = TrajectoryStore(Path(args.db))

    dispatch = {
        "stats":   cmd_stats,
        "compact": cmd_compact,
        "export":  cmd_export,
        "retrain": cmd_retrain,
        "watch":   cmd_watch,
        "tail":    cmd_tail,
        "tasks":   cmd_tasks,
        "schema":  cmd_schema,
    }
    dispatch[args.cmd](store, args)


if __name__ == "__main__":
    main()
