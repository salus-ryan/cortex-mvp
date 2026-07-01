"""
learner.py — Cortex Continuous Learning Loop

Polls the retrain sentinel file written by the Compactor and triggers LoRA
fine-tuning whenever the dataset has grown enough to warrant a new checkpoint.

The flywheel
------------

    ┌─────────────────────────────────────────────────────────────────┐
    │                                                                 │
    │  runtime.run_task()                                             │
    │      └─ store.log_step()  ──────────────────────────────────┐  │
    │                                                             ▼  │
    │  compactor.compact("incremental")                           │  │
    │      └─ quality_score / dedup / export sft_train.jsonl      │  │
    │      └─ writes data/.retrain_needed  ◄──────────────────────┘  │
    │                                                                 │
    │  learner.watch()  [background thread or cron]                  │
    │      └─ detects data/.retrain_needed                           │
    │      └─ runs lora_finetune.py  →  models/cortex-lora-<n>/      │
    │      └─ updates data/.current_checkpoint                       │
    │      └─ runtime picks up new checkpoint on next task           │
    │                                                                 │
    └─────────────────────────────────────────────────────────────────┘

Usage
-----
    # One-shot: compact + retrain if needed
    python3 -m cortex.learner --once

    # Daemon: poll every 5 minutes
    python3 -m cortex.learner --watch --interval 300

    # Force retrain regardless of sentinel
    python3 -m cortex.learner --force

    # Just compact, no training
    python3 -m cortex.learner --compact-only --strategy incremental
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from cortex.store import TrajectoryStore
from cortex.compactor import Compactor

_SENTINEL     = Path("data/.retrain_needed")
_CHECKPOINT   = Path("data/.current_checkpoint")
_MODELS_DIR   = Path("models")
_SFT_DIR      = Path("data/sft")
_DB_PATH      = Path("data/cortex.db")
_SCRIPT       = Path("scripts/lora_finetune.py")


def _read_sentinel() -> Optional[dict]:
    if _SENTINEL.exists():
        try:
            return json.loads(_SENTINEL.read_text())
        except Exception:
            return {}
    return None


def _clear_sentinel() -> None:
    if _SENTINEL.exists():
        _SENTINEL.unlink()


def _next_checkpoint_dir() -> Path:
    """Return models/cortex-lora-<n+1>/ where n is the current checkpoint index."""
    existing = sorted(_MODELS_DIR.glob("cortex-lora-*"))
    idx = len(existing) + 1
    return _MODELS_DIR / f"cortex-lora-{idx:04d}"


def _current_checkpoint() -> Optional[str]:
    if _CHECKPOINT.exists():
        return _CHECKPOINT.read_text().strip()
    # Fall back to models/cortex-lora if it exists
    fallback = _MODELS_DIR / "cortex-lora"
    if fallback.exists():
        return str(fallback)
    return None


def compact(store: TrajectoryStore, strategy: str = "incremental") -> dict:
    """Run a compaction pass and return the result dict."""
    c = Compactor(store, output_dir=_SFT_DIR)
    result = c.compact_recursive(strategy=strategy)
    return result[-1] if result else {}


def retrain(
    train_path: str,
    val_path: str,
    model_base: Optional[str] = None,
    epochs: int = 1,
    batch_size: int = 4,
) -> str:
    """
    Launch lora_finetune.py as a subprocess.

    If a previous checkpoint exists, we fine-tune from that checkpoint
    (continual learning — not from the frozen base model each time).

    Returns the path to the new checkpoint directory.
    """
    output_dir = _next_checkpoint_dir()
    output_dir.parent.mkdir(parents=True, exist_ok=True)

    # Prefer the latest checkpoint as the starting point
    base = model_base or _current_checkpoint() or "Qwen/Qwen2.5-0.5B-Instruct"

    cmd = [
        sys.executable, str(_SCRIPT),
        "--model",      base,
        "--train",      train_path,
        "--val",        val_path,
        "--output",     str(output_dir),
        "--epochs",     str(epochs),
        "--batch_size", str(batch_size),
    ]

    print(f"[learner] Starting retrain: {' '.join(cmd)}")
    result = subprocess.run(cmd, check=True)

    if result.returncode == 0:
        _CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
        _CHECKPOINT.write_text(str(output_dir))
        print(f"[learner] New checkpoint: {output_dir}")
    else:
        print(f"[learner] Training failed with code {result.returncode}")

    return str(output_dir)


def run_once(
    store: TrajectoryStore,
    strategy: str = "incremental",
    force: bool = False,
    compact_only: bool = False,
    epochs: int = 1,
    batch_size: int = 4,
    model_base: Optional[str] = None,
) -> dict:
    """Compact, then retrain if the sentinel is set (or force=True)."""
    print(f"[learner] Compacting ({strategy})...")
    compact_result = compact(store, strategy=strategy)
    print(f"[learner] Compact: {compact_result.get('rows_in',0)} in → "
          f"{compact_result.get('rows_out',0)} out")

    if compact_only:
        return compact_result

    sentinel = _read_sentinel()
    if sentinel is None and not force:
        print("[learner] No retrain needed.")
        return compact_result

    train_path = compact_result.get("train_path") or str(_SFT_DIR / "sft_train.jsonl")
    val_path   = compact_result.get("val_path")   or str(_SFT_DIR / "sft_val.jsonl")

    if not Path(train_path).exists():
        print(f"[learner] Train file not found: {train_path}. Skipping retrain.")
        return compact_result

    _clear_sentinel()
    checkpoint = retrain(
        train_path=train_path,
        val_path=val_path,
        model_base=model_base,
        epochs=epochs,
        batch_size=batch_size,
    )
    compact_result["checkpoint"] = checkpoint
    return compact_result


def watch(
    store: TrajectoryStore,
    interval: int = 300,
    strategy: str = "incremental",
    epochs: int = 1,
    batch_size: int = 4,
    model_base: Optional[str] = None,
) -> None:
    """Poll for the retrain sentinel and trigger retraining when found."""
    print(f"[learner] Watching for retrain signal every {interval}s...")
    while True:
        try:
            run_once(
                store,
                strategy=strategy,
                epochs=epochs,
                batch_size=batch_size,
                model_base=model_base,
            )
        except Exception as e:
            print(f"[learner] Error: {e}")
        time.sleep(interval)


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli() -> None:
    parser = argparse.ArgumentParser(
        description="Cortex continuous learning loop",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--db",           default=str(_DB_PATH),  help="SQLite DB path")
    parser.add_argument("--strategy",     default="incremental",
                        choices=["incremental", "full", "quality_filter"])
    parser.add_argument("--epochs",       type=int,   default=1)
    parser.add_argument("--batch-size",   type=int,   default=4)
    parser.add_argument("--model",        default=None, help="Override base model")
    parser.add_argument("--interval",     type=int,   default=300,
                        help="Poll interval in seconds (--watch mode)")

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once",         action="store_true",
                      help="Compact + retrain once if sentinel is set, then exit")
    mode.add_argument("--watch",        action="store_true",
                      help="Run as a daemon, polling every --interval seconds")
    mode.add_argument("--force",        action="store_true",
                      help="Compact + force retrain regardless of sentinel")
    mode.add_argument("--compact-only", action="store_true",
                      help="Compact only, do not retrain")

    args = parser.parse_args()
    store = TrajectoryStore(Path(args.db))

    if args.watch:
        watch(store, interval=args.interval, strategy=args.strategy,
              epochs=args.epochs, batch_size=args.batch_size, model_base=args.model)
    else:
        result = run_once(
            store,
            strategy=args.strategy,
            force=args.force,
            compact_only=args.compact_only,
            epochs=args.epochs,
            batch_size=args.batch_size,
            model_base=args.model,
        )
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    _cli()
