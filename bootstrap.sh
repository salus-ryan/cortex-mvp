#!/usr/bin/env bash
# =============================================================================
# bootstrap.sh — Cortex MVP: one command to install, test, generate data,
#                prepare SFT dataset, and launch LoRA fine-tuning.
#
# Usage:
#   ./bootstrap.sh                          # auto-detect GPU, default model
#   ./bootstrap.sh --model Qwen/Qwen2.5-0.5B-Instruct
#   ./bootstrap.sh --model microsoft/Phi-3-mini-4k-instruct --epochs 5
#   ./bootstrap.sh --cpu                    # CPU-only dry-run (no training)
#   ./bootstrap.sh --data-only              # generate data + SFT prep, skip training
#   ./bootstrap.sh --skip-tests             # skip pytest (faster iteration)
#
# Recommended base models (small, fast, local):
#   Qwen/Qwen2.5-0.5B-Instruct             (fastest, ~1 GB VRAM)
#   Qwen/Qwen2.5-1.5B-Instruct             (good balance)
#   microsoft/Phi-3-mini-4k-instruct        (3.8B, strong reasoning)
#   google/gemma-2-2b-it                    (2B, Apache 2.0)
#   meta-llama/Llama-3.2-1B-Instruct       (1B, needs HF token)
#   deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B
# =============================================================================

set -euo pipefail

# ── Defaults ──────────────────────────────────────────────────────────────────
MODEL="Qwen/Qwen2.5-0.5B-Instruct"
EPOCHS=3
BATCH_SIZE=4
LR="2e-4"
LORA_R=16
DATA_COUNT=200
SKIP_TESTS=false
DATA_ONLY=false
CPU_ONLY=false
OUTPUT_DIR="models/cortex-lora"
VENV_DIR=".venv"

# ── Colour helpers ─────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[cortex]${NC} $*"; }
warn()  { echo -e "${YELLOW}[cortex]${NC} $*"; }
error() { echo -e "${RED}[cortex]${NC} $*" >&2; }

# ── Argument parsing ───────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --model)       MODEL="$2";      shift 2 ;;
    --epochs)      EPOCHS="$2";     shift 2 ;;
    --batch-size)  BATCH_SIZE="$2"; shift 2 ;;
    --lr)          LR="$2";         shift 2 ;;
    --lora-r)      LORA_R="$2";     shift 2 ;;
    --count)       DATA_COUNT="$2"; shift 2 ;;
    --output)      OUTPUT_DIR="$2"; shift 2 ;;
    --venv)        VENV_DIR="$2";   shift 2 ;;
    --skip-tests)  SKIP_TESTS=true; shift ;;
    --data-only)   DATA_ONLY=true;  shift ;;
    --cpu)         CPU_ONLY=true;   shift ;;
    -h|--help)
      sed -n '/^# Usage/,/^# ====/p' "$0" | head -20
      exit 0 ;;
    *) error "Unknown option: $1"; exit 1 ;;
  esac
done

# ── Banner ─────────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║              CORTEX MVP — BOOTSTRAP + TRAIN                  ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
info "Model:      $MODEL"
info "Epochs:     $EPOCHS"
info "Batch size: $BATCH_SIZE"
info "LR:         $LR"
info "Data count: $DATA_COUNT"
info "Output:     $OUTPUT_DIR"
echo ""

# ── Step 0: Python check ───────────────────────────────────────────────────────
info "Step 0/6 — Checking Python..."

# Find python3.11+ (prefer 3.11/3.12, fall back to python3)
PYTHON=""
for candidate in python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" &>/dev/null; then
    PYTHON=$(command -v "$candidate")
    break
  fi
done

if [[ -z "$PYTHON" ]]; then
  error "Python 3 not found. Install it with: sudo apt install python3 python3-venv python3-full"
  exit 1
fi

PY_VERSION=$("$PYTHON" --version 2>&1)
info "  Found: $PY_VERSION at $PYTHON"

# Check python3-venv / venv module is available
if ! "$PYTHON" -m venv --help &>/dev/null; then
  error "python3-venv is not installed."
  error "Fix with: sudo apt install python3-venv python3-full"
  exit 1
fi

# ── Step 1: Create and activate venv ──────────────────────────────────────────
info "Step 1/6 — Setting up virtual environment..."

if [[ ! -d "$VENV_DIR" ]]; then
  info "  Creating venv at $VENV_DIR ..."
  "$PYTHON" -m venv "$VENV_DIR"
else
  info "  Reusing existing venv at $VENV_DIR"
fi

# Activate
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
PYTHON="$VENV_DIR/bin/python"
PIP="$VENV_DIR/bin/pip"

info "  Activated: $("$PYTHON" --version)"

# Upgrade pip silently
"$PIP" install --quiet --upgrade pip

# Install core requirements (always)
info "  Installing requirements.txt (core runtime + pytest)..."
"$PIP" install --quiet jsonschema pytest

# Install training stack unless skipped
if [[ "$DATA_ONLY" == false && "$CPU_ONLY" == false ]]; then
  info "  Installing training stack (transformers, peft, trl, datasets, accelerate)..."
  "$PIP" install --quiet \
    transformers \
    peft \
    trl \
    datasets \
    accelerate \
    bitsandbytes \
    sentencepiece \
    protobuf
  info "  Training stack installed."
elif [[ "$CPU_ONLY" == true ]]; then
  warn "  --cpu flag set: skipping training stack."
elif [[ "$DATA_ONLY" == true ]]; then
  warn "  --data-only flag set: skipping training stack."
fi

# ── Step 2: Run tests ──────────────────────────────────────────────────────────
if [[ "$SKIP_TESTS" == false ]]; then
  info "Step 2/6 — Running test suite..."
  "$PYTHON" -m pytest tests/ -q --tb=short
  info "  All tests passed."
else
  warn "Step 2/6 — Tests skipped (--skip-tests)."
fi

# ── Step 3: Generate synthetic data ───────────────────────────────────────────
info "Step 3/6 — Generating synthetic training data ($DATA_COUNT samples)..."
mkdir -p data
"$PYTHON" scripts/generate_data.py --output data/ --count "$DATA_COUNT" --seed 42
info "  Generated: data/train_positive.jsonl, data/train_negative.jsonl"

# ── Step 4: Prepare SFT dataset ───────────────────────────────────────────────
info "Step 4/6 — Preparing SFT dataset..."
"$PYTHON" - <<'PYEOF'
import sys
from pathlib import Path
sys.path.insert(0, ".")
from cortex.trainer import prepare_sft_dataset

train_path, val_path = prepare_sft_dataset(
    positive_path=Path("data/train_positive.jsonl"),
    output_path=Path("data/sft"),
    negative_path=Path("data/train_negative.jsonl"),
    val_split=0.1,
)
print(f"  Train: {train_path}  ({sum(1 for _ in open(train_path))} samples)")
print(f"  Val:   {val_path}  ({sum(1 for _ in open(val_path))} samples)")
PYEOF
info "  SFT dataset ready."

# ── Step 5: Write LoRA script ──────────────────────────────────────────────────
info "Step 5/6 — Writing LoRA fine-tuning script..."
"$PYTHON" - <<'PYEOF'
import sys
from pathlib import Path
sys.path.insert(0, ".")
from cortex.trainer import write_lora_script
p = write_lora_script(Path("scripts/"))
print(f"  Script written: {p}")
PYEOF

# ── Step 6: Train ─────────────────────────────────────────────────────────────
if [[ "$DATA_ONLY" == true ]]; then
  warn "Step 6/6 — Training skipped (--data-only)."
  echo ""
  info "Data is ready. To train, run:"
  echo ""
  echo "  source $VENV_DIR/bin/activate"
  echo "  python scripts/lora_finetune.py \\"
  echo "    --model $MODEL \\"
  echo "    --train data/sft/sft_train.jsonl \\"
  echo "    --val   data/sft/sft_val.jsonl \\"
  echo "    --output $OUTPUT_DIR \\"
  echo "    --epochs $EPOCHS"
  echo ""
  exit 0
fi

if [[ "$CPU_ONLY" == true ]]; then
  warn "Step 6/6 — Training skipped (--cpu). Dry-run complete."
  echo ""
  info "To train on a GPU machine, run:"
  echo ""
  echo "  ./bootstrap.sh --model $MODEL --epochs $EPOCHS"
  echo ""
  exit 0
fi

info "Step 6/6 — Launching LoRA fine-tuning..."
echo ""
echo "  Base model:  $MODEL"
echo "  Train file:  data/sft/sft_train.jsonl"
echo "  Val file:    data/sft/sft_val.jsonl"
echo "  Output dir:  $OUTPUT_DIR"
echo "  Epochs:      $EPOCHS"
echo ""

mkdir -p "$OUTPUT_DIR"

"$PYTHON" scripts/lora_finetune.py \
  --model      "$MODEL" \
  --train      data/sft/sft_train.jsonl \
  --val        data/sft/sft_val.jsonl \
  --output     "$OUTPUT_DIR" \
  --epochs     "$EPOCHS" \
  --batch_size "$BATCH_SIZE" \
  --lr         "$LR" \
  --lora_r     "$LORA_R"

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║                   TRAINING COMPLETE                          ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
info "Model saved to: $OUTPUT_DIR"
echo ""
info "To run the evaluation benchmark:"
echo ""
echo "  source $VENV_DIR/bin/activate"
echo "  python -c \""
echo "  import sys; sys.path.insert(0, '.')"
echo "  from transformers import pipeline"
echo "  from cortex.eval import run_eval"
echo "  pipe = pipeline('text-generation', model='$OUTPUT_DIR', max_new_tokens=64)"
echo "  def model_fn(p): return pipe(p)[0]['generated_text'][len(p):]"
echo "  run_eval(model_fn)"
echo "  \""
echo ""
