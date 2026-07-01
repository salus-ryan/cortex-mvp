#!/usr/bin/env python3
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
    python3 scripts/lora_finetune.py \
        --model Qwen/Qwen2.5-0.5B-Instruct \
        --train data/sft/sft_train.jsonl \
        --val data/sft/sft_val.jsonl \
        --output models/cortex-lora \
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
    _use_bf16 = (torch.cuda.is_available() and torch.cuda.is_bf16_supported()) or                 (hasattr(torch.backends, "mps") and torch.backends.mps.is_available())

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
