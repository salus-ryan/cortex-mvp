"""
trainer.py — Cortex Supervised Fine-Tuning Trainer

Implements the 7-phase training recipe:

  Phase 0 — Build deterministic runtime harness (done)
  Phase 1 — Generate synthetic trajectories (generate_data.py)
  Phase 2 — Supervised fine-tune on next SCL action prediction (this module)
  Phase 3 — Run model in harness and collect trajectories
  Phase 4 — Keep verified successful trajectories and retrain
  Phase 5 — Add negative examples and preference data
  Phase 6 — Add broken-runtime and broken-repo repair tasks
  Phase 7 — Evaluate on held-out tasks (eval.py)

SFT input format:
  SYSTEM:
  You are Cortex policy. Emit exactly one valid SCL control record. Do not emit prose.

  GOAL:
  ...

  STATE:
  ...

  MEMORY_SUMMARY:
  ...

  BUDGET:
  ...

  TOOL_MANIFEST:
  ...

  LATEST_OBSERVATION:
  ...

  NEXT_ACTION:

Target: @tool → call [name: "pytest", args: "tests/test_budget.py -q", risk: "verify"]

The trainer formats samples into this prompt structure and calls the
HuggingFace transformers + PEFT LoRA fine-tuning pipeline.

For the MVP, this module also provides a stub that works with any
OpenAI-compatible API for quick iteration without local GPU.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Prompt formatting
# ---------------------------------------------------------------------------

def format_prompt(sample: dict) -> str:
    """
    Format a training sample into the SFT prompt structure.

    Args:
        sample: Dict with keys: goal, state, memory_summary (or memory),
                budget, tool_manifest, observation.

    Returns:
        Formatted prompt string ending with 'NEXT_ACTION:'.
    """
    goal = sample.get("goal", "")
    state = sample.get("state", {})
    memory = sample.get("memory_summary") or sample.get("memory", {})
    budget = sample.get("budget", {})
    tools = sample.get("tool_manifest", [])
    observation = sample.get("observation", "")

    state_str = json.dumps(state, indent=2) if isinstance(state, dict) else str(state)
    memory_str = json.dumps(memory, indent=2) if isinstance(memory, dict) else str(memory)
    budget_str = json.dumps(budget, indent=2) if isinstance(budget, dict) else str(budget)
    tools_str = ", ".join(tools) if isinstance(tools, list) else str(tools)

    return (
        "SYSTEM:\n"
        "You are Cortex policy. Emit exactly one valid SCL control record. Do not emit prose.\n\n"
        f"GOAL:\n{goal}\n\n"
        f"STATE:\n{state_str}\n\n"
        f"MEMORY_SUMMARY:\n{memory_str}\n\n"
        f"BUDGET:\n{budget_str}\n\n"
        f"TOOL_MANIFEST:\n{tools_str}\n\n"
        f"LATEST_OBSERVATION:\n{observation}\n\n"
        "NEXT_ACTION:"
    )


def format_training_pair(sample: dict) -> dict:
    """
    Format a sample into a (prompt, completion) pair for SFT.

    Returns:
        Dict with 'prompt' and 'completion' keys.
    """
    return {
        "prompt": format_prompt(sample),
        "completion": " " + sample.get("target", ""),
    }


def load_jsonl(path: Path) -> list[dict]:
    """Load a JSONL file into a list of dicts."""
    samples = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                samples.append(json.loads(line))
    return samples


def save_jsonl(samples: list[dict], path: Path) -> None:
    """Save a list of dicts to a JSONL file."""
    with path.open("w") as f:
        for s in samples:
            f.write(json.dumps(s) + "\n")


def prepare_sft_dataset(
    positive_path: Path,
    output_path: Path,
    negative_path: Optional[Path] = None,
    semantic_path: Optional[Path] = None,
    val_split: float = 0.1,
    semantic_weight: float = 0.4,
) -> tuple[Path, Path]:
    """
    Prepare the SFT dataset from positive (and optionally negative) samples.

    Formats each sample into (prompt, completion) pairs and splits into
    train/val sets.

    Args:
        positive_path: Path to positive JSONL samples.
        output_path: Directory to write train/val files.
        negative_path: Optional path to negative JSONL samples.
        val_split: Fraction of data to use for validation.

    Returns:
        Tuple of (train_path, val_path).
    """
    import random

    output_path.mkdir(parents=True, exist_ok=True)
    samples = load_jsonl(positive_path)

    # Format into SFT pairs
    pairs = [format_training_pair(s) for s in samples]

    # Optionally add negative examples as preference pairs
    if negative_path and negative_path.exists():
        neg_samples = load_jsonl(negative_path)
        for neg in neg_samples:
            # For DPO/preference training: include as rejected completions
            pairs.append({
                "prompt": format_prompt(neg),
                "completion": " " + neg.get("bad_action", ""),
                "is_negative": True,
                "denial_reason": neg.get("denial_reason", ""),
            })

    # Incorporate semantic signal pairs (authority model training)
    if semantic_path and Path(semantic_path).exists():
        sem_samples = load_jsonl(Path(semantic_path))
        # Semantic pairs already have prompt+completion fields
        n_semantic = int(len(pairs) * semantic_weight / max(1 - semantic_weight, 0.01))
        n_semantic = min(n_semantic, len(sem_samples))
        import random as _random
        selected = _random.sample(sem_samples, n_semantic)
        for s in selected:
            if "prompt" in s and "completion" in s:
                pairs.append({"prompt": s["prompt"], "completion": s["completion"],
                               "pair_type": s.get("pair_type", "semantic"),
                               "admissible": s.get("admissible", True)})

    random.shuffle(pairs)
    split_idx = max(1, int(len(pairs) * (1 - val_split)))
    train_pairs = pairs[:split_idx]
    val_pairs = pairs[split_idx:]

    train_path = output_path / "sft_train.jsonl"
    val_path = output_path / "sft_val.jsonl"

    save_jsonl(train_pairs, train_path)
    save_jsonl(val_pairs, val_path)

    print(f"SFT dataset: {len(train_pairs)} train, {len(val_pairs)} val")
    return train_path, val_path


# ---------------------------------------------------------------------------
# LoRA fine-tuning launcher (requires transformers + peft + trl)
# ---------------------------------------------------------------------------

# LORA_TRAINING_SCRIPT removed — canonical script lives in scripts/lora_finetune.py
LORA_TRAINING_SCRIPT = None  # not used; write_lora_script copies the canonical file


def write_lora_script(output_dir: Path) -> Path:
    """Write the LoRA fine-tuning script to disk.

    The canonical source of truth is scripts/lora_finetune.py in the repo root.
    This function either leaves it in place (if output_dir == scripts/) or
    copies it to the requested location.
    """
    import shutil
    script_path = output_dir / "lora_finetune.py"

    canonical = Path(__file__).parent.parent / "scripts" / "lora_finetune.py"
    if canonical.resolve() == script_path.resolve():
        # Already the canonical file — nothing to do
        pass
    elif canonical.exists():
        shutil.copy2(canonical, script_path)
    else:
        raise FileNotFoundError(
            f"Canonical lora_finetune.py not found at {canonical}. "
            "Ensure the repo is fully cloned."
        )

    script_path.chmod(0o755)
    return script_path


# ---------------------------------------------------------------------------
# OpenAI-compatible stub model for rapid iteration
# ---------------------------------------------------------------------------

class StubModel:
    """
    A stub policy model that uses an OpenAI-compatible API.

    Useful for testing the runtime harness without a local GPU.
    Replace with a LoRA-fine-tuned local model for production.
    """

    def __init__(self, api_base: Optional[str] = None, model: str = "gpt-4o-mini") -> None:
        self.model = model
        self._client = None
        self._api_base = api_base

    def _get_client(self):
        if self._client is None:
            try:
                from openai import OpenAI
                kwargs: dict = {}
                if self._api_base:
                    kwargs["base_url"] = self._api_base
                self._client = OpenAI(**kwargs)
            except ImportError:
                raise RuntimeError("openai package required: pip install openai")
        return self._client

    def __call__(self, prompt: str) -> str:
        """Generate a single SCL action from the prompt."""
        client = self._get_client()
        response = client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=128,
            temperature=0.1,
            stop=["\n\n"],
        )
        return response.choices[0].message.content.strip()
