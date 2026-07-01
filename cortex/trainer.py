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
    val_split: float = 0.1,
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

LORA_TRAINING_SCRIPT = '''#!/usr/bin/env python3
"""
lora_finetune.py — LoRA fine-tuning script for Cortex policy model.

Requires:
    pip install transformers peft trl datasets accelerate bitsandbytes

Recommended base models (0.5B–3B):
    - Qwen/Qwen2.5-0.5B-Instruct
    - microsoft/Phi-3-mini-4k-instruct
    - google/gemma-2-2b-it
    - meta-llama/Llama-3.2-1B-Instruct
    - deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B

Usage:
    python3 scripts/lora_finetune.py \\
        --model Qwen/Qwen2.5-0.5B-Instruct \\
        --train data/sft/sft_train.jsonl \\
        --val data/sft/sft_val.jsonl \\
        --output models/cortex-lora \\
        --epochs 3
"""

import argparse
import json
from pathlib import Path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="Qwen/Qwen2.5-0.5B-Instruct")
    parser.add_argument("--train", required=True)
    parser.add_argument("--val", required=True)
    parser.add_argument("--output", default="models/cortex-lora")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch_size", type=int, default=4)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--lora_r", type=int, default=16)
    parser.add_argument("--lora_alpha", type=int, default=32)
    parser.add_argument("--max_seq_len", type=int, default=1024)
    args = parser.parse_args()

    try:
        from transformers import AutoModelForCausalLM, AutoTokenizer, TrainingArguments
        from peft import LoraConfig, get_peft_model, TaskType
        from trl import SFTTrainer
        from datasets import Dataset
    except ImportError as e:
        print(f"Missing dependency: {e}")
        print("Install with: pip install transformers peft trl datasets accelerate")
        return

    print(f"Loading base model: {args.model}")
    tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        args.model,
        trust_remote_code=True,
        torch_dtype="auto",
        device_map="auto",
    )

    # LoRA configuration
    lora_config = LoraConfig(
        task_type=TaskType.CAUSAL_LM,
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        target_modules=["q_proj", "v_proj", "k_proj", "o_proj"],
        lora_dropout=0.05,
        bias="none",
    )
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    # Load datasets
    def load_jsonl(path):
        with open(path) as f:
            return [json.loads(l) for l in f if l.strip()]

    def format_sample(s):
        return {"text": s["prompt"] + s["completion"]}

    train_data = Dataset.from_list([format_sample(s) for s in load_jsonl(args.train)])
    val_data = Dataset.from_list([format_sample(s) for s in load_jsonl(args.val)])

    # Training arguments — handle both old (<4.46) and new (>=4.46) transformers APIs
    import transformers as _tf
    import torch
    _tf_version = tuple(int(x) for x in _tf.__version__.split(".")[:2])
    _use_new_api = _tf_version >= (4, 46)

    # fp16 only on CUDA; bf16 on Ampere+ CUDA or MPS; otherwise plain float
    _use_fp16 = torch.cuda.is_available() and not torch.cuda.is_bf16_supported()
    _use_bf16 = (torch.cuda.is_available() and torch.cuda.is_bf16_supported()) or \
                (hasattr(torch.backends, "mps") and torch.backends.mps.is_available())

    _eval_kwarg = {"eval_strategy": "steps"} if _use_new_api else {"evaluation_strategy": "steps"}

    training_args = TrainingArguments(
        output_dir=args.output,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.batch_size,
        learning_rate=args.lr,
        warmup_ratio=0.05,
        lr_scheduler_type="cosine",
        logging_steps=10,
        eval_steps=50,
        save_steps=100,
        **_eval_kwarg,
        save_total_limit=2,
        load_best_model_at_end=True,
        report_to="none",
        fp16=_use_fp16,
        bf16=_use_bf16,
    )

    # SFTTrainer API: tokenizer kwarg renamed to processing_class in trl>=0.12
    import trl as _trl
    _trl_version = tuple(int(x) for x in _trl.__version__.split(".")[:2])
    _tok_kwarg = "processing_class" if _trl_version >= (0, 12) else "tokenizer"

    trainer = SFTTrainer(
        model=model,
        args=training_args,
        train_dataset=train_data,
        eval_dataset=val_data,
        **{_tok_kwarg: tokenizer},
        max_seq_length=args.max_seq_len,
        dataset_text_field="text",
    )

    print("Starting LoRA fine-tuning...")
    trainer.train()
    trainer.save_model(args.output)
    tokenizer.save_pretrained(args.output)
    print(f"Model saved to {args.output}")


if __name__ == "__main__":
    main()
'''


def write_lora_script(output_dir: Path) -> Path:
    """Write the LoRA fine-tuning script to disk."""
    script_path = output_dir / "lora_finetune.py"
    script_path.write_text(LORA_TRAINING_SCRIPT)
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
