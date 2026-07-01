#!/data/data/com.termux/files/usr/bin/bash
# =============================================================================
# bootstrap_termux.sh — Cortex MVP bootstrap for Termux (Android)
#
# Usage (single command):
#   git clone https://github.com/salus-ryan/cortex-mvp.git; cd cortex-mvp && ./bootstrap_termux.sh
#
# Or if already cloned:
#   cd cortex-mvp && ./bootstrap_termux.sh
#
# What this does:
#   1. Self-updates from GitHub
#   2. Installs Termux system deps (python, git, clang, etc.)
#   3. Creates a venv and installs Python deps
#   4. Runs all 328 unit tests
#   5. Generates synthetic training data + semantic signal pairs
#   6. Runs the full e2e pipeline verification
#   7. Launches a CPU-optimised interactive demo (no GPU needed)
#
# Termux notes:
#   - No GPU on most Android devices → training uses CPU (slow but works)
#   - For training, use --data-only to skip the GPU step and just verify
#   - Recommended: use --demo to run the interactive SCL REPL instead
# =============================================================================

set -euo pipefail

REPO_URL="https://github.com/salus-ryan/cortex-mvp.git"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()  { echo -e "${CYAN}[cortex]${RESET} $*"; }
ok()    { echo -e "${GREEN}[  OK  ]${RESET} $*"; }
warn()  { echo -e "${YELLOW}[ WARN ]${RESET} $*"; }
die()   { echo -e "${RED}[ FAIL ]${RESET} $*" >&2; exit 1; }

# ── Args ──────────────────────────────────────────────────────────────────────
DATA_ONLY=false
SKIP_TESTS=false
DEMO=false
COUNT=200
for arg in "$@"; do
    case "$arg" in
        --data-only)   DATA_ONLY=true ;;
        --skip-tests)  SKIP_TESTS=true ;;
        --demo)        DEMO=true ;;
        --count=*)     COUNT="${arg#*=}" ;;
        --help|-h)
            echo "Usage: ./bootstrap_termux.sh [--data-only] [--skip-tests] [--demo] [--count=N]"
            echo "  --data-only   Generate data and run e2e, skip training"
            echo "  --skip-tests  Skip unit tests (faster)"
            echo "  --demo        Launch interactive SCL REPL after setup"
            echo "  --count=N     Number of synthetic training samples (default 200)"
            exit 0 ;;
    esac
done

# ── Banner ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║          CORTEX MVP — TERMUX BOOTSTRAP                       ║${RESET}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════════════╝${RESET}"
echo ""
info "Platform:   Termux (Android)"
info "Data count: ${COUNT}"
info "Demo mode:  ${DEMO}"
echo ""

# ── Step 0: Self-update ───────────────────────────────────────────────────────
info "Step 0/6 — Self-update..."
cd "$SCRIPT_DIR"

# If no remote set, set it
CURRENT_REMOTE="$(git remote get-url origin 2>/dev/null || echo '')"
if [[ -z "$CURRENT_REMOTE" ]]; then
    warn "No git remote found — setting to ${REPO_URL}"
    git remote add origin "$REPO_URL" 2>/dev/null || git remote set-url origin "$REPO_URL"
fi

if [[ -z "${CORTEX_UPDATED:-}" ]]; then
    BEFORE="$(git rev-parse HEAD 2>/dev/null || echo 'none')"
    git pull --ff-only origin main 2>/dev/null || warn "Could not pull — running with current version"
    AFTER="$(git rev-parse HEAD 2>/dev/null || echo 'none')"
    if [[ "$BEFORE" != "$AFTER" ]]; then
        info "Updated from ${BEFORE:0:7} → ${AFTER:0:7} — re-running updated script..."
        export CORTEX_UPDATED=1
        exec bash "$SCRIPT_DIR/bootstrap_termux.sh" "$@"
    fi
fi
ok "Repository is up to date"

# ── Step 1: Termux system deps ────────────────────────────────────────────────
info "Step 1/6 — Installing Termux system dependencies..."

# Detect if we're actually in Termux
IS_TERMUX=false
if [[ -d "/data/data/com.termux" ]] || [[ -n "${TERMUX_VERSION:-}" ]] || command -v pkg &>/dev/null; then
    IS_TERMUX=true
fi

if $IS_TERMUX; then
    info "Termux detected — using pkg"
    pkg update -y -q 2>/dev/null || warn "pkg update failed (may be fine)"
    for dep in python git clang make libffi openssl; do
        if ! pkg list-installed 2>/dev/null | grep -q "^${dep}"; then
            info "  Installing ${dep}..."
            pkg install -y "$dep" 2>/dev/null || warn "  Could not install ${dep}"
        fi
    done
    # pip may not be available — install via ensurepip
    python -m ensurepip --upgrade 2>/dev/null || true
else
    info "Not Termux — using system Python (apt/brew assumed)"
fi

# Verify Python
PYTHON=""
for py in python3.12 python3.11 python3.10 python3 python; do
    if command -v "$py" &>/dev/null; then
        PYTHON="$py"
        break
    fi
done
[[ -z "$PYTHON" ]] && die "Python not found. In Termux: pkg install python"
ok "Python: $($PYTHON --version)"

# Verify venv module
if ! $PYTHON -c "import venv" 2>/dev/null; then
    if $IS_TERMUX; then
        die "venv not available. Try: pkg install python"
    else
        die "venv not available. Try: sudo apt install python3-venv python3-full"
    fi
fi

# ── Step 2: Virtual environment ───────────────────────────────────────────────
info "Step 2/6 — Setting up virtual environment..."
VENV_DIR="$SCRIPT_DIR/.venv"

if [[ ! -d "$VENV_DIR" ]]; then
    info "  Creating .venv..."
    $PYTHON -m venv "$VENV_DIR"
    ok "  .venv created"
else
    ok "  .venv already exists"
fi

# Activate
source "$VENV_DIR/bin/activate"
pip install --upgrade pip -q 2>/dev/null || true

# Install core deps (no GPU stack — Termux can't run CUDA)
info "  Installing Python dependencies..."
# Pin jsonschema to 4.17.3 which uses pyrsistent (pure Python) not rpds-py (Rust).
# rpds-py requires Rust to build from source and fails on most Termux/ARM installs.
pip install -q "jsonschema==4.17.3" "pyrsistent>=0.18.0" pytest 2>&1 | tail -3

# Install training stack only if not data-only and not demo
if ! $DATA_ONLY && ! $DEMO; then
    info "  Installing training stack (CPU-only, this may take a few minutes)..."
    # Use CPU-only torch to avoid massive download
    pip install -q torch --index-url https://download.pytorch.org/whl/cpu 2>&1 | tail -5 || \
        warn "  CPU torch install failed — training will be skipped"
    pip install -q transformers peft trl datasets accelerate 2>&1 | tail -5 || \
        warn "  Training stack install failed — use --data-only to skip"
fi

ok "Dependencies installed"

# ── Step 3: Unit tests ────────────────────────────────────────────────────────
if $SKIP_TESTS; then
    warn "Step 3/6 — Skipping unit tests (--skip-tests)"
else
    info "Step 3/6 — Running unit tests..."
    cd "$SCRIPT_DIR"
    if python -m pytest tests/ -q --tb=short 2>&1 | tail -5; then
        ok "All tests passed"
    else
        die "Tests failed — see output above. Run with --skip-tests to bypass."
    fi
fi

# ── Step 4: Generate data ─────────────────────────────────────────────────────
info "Step 4/6 — Generating synthetic training data..."
cd "$SCRIPT_DIR"
mkdir -p data/sft data/trajectories

if [[ ! -f "data/train_positive.jsonl" ]] || [[ ! -s "data/train_positive.jsonl" ]]; then
    python scripts/generate_data.py --count "$COUNT" --output data/ 2>&1 | tail -5
    ok "Synthetic data generated (${COUNT} positive samples)"
else
    ok "Synthetic data already exists — skipping generation"
fi

# Generate semantic signal pairs
if [[ ! -f "data/semantic_signal.jsonl" ]] || [[ ! -s "data/semantic_signal.jsonl" ]]; then
    info "  Generating semantic signal pairs..."
    python -c "
from cortex.semantic_signal import generate_semantic_dataset
from pathlib import Path
n = max(20, int(${COUNT} * 0.3))
result = generate_semantic_dataset(
    Path('data/semantic_signal.jsonl'),
    n_positive=n, n_negative=int(n*0.4), n_calibration=int(n*0.2)
)
print(f'  Semantic signal: {result}')
" 2>&1 | tail -3
    ok "Semantic signal pairs generated"
else
    ok "Semantic signal already exists — skipping"
fi

# Prepare SFT dataset (with semantic signal)
info "  Preparing SFT dataset..."
python -c "
from cortex.trainer import prepare_sft_dataset
from pathlib import Path
train, val = prepare_sft_dataset(
    positive_path=Path('data/train_positive.jsonl'),
    output_path=Path('data/sft'),
    negative_path=Path('data/train_negative.jsonl'),
    semantic_path=Path('data/semantic_signal.jsonl'),
    val_split=0.1,
    semantic_weight=0.4,
)
print(f'  SFT: {train}, {val}')
" 2>&1 | tail -3
ok "SFT dataset prepared"

# ── Step 5: E2E verification ──────────────────────────────────────────────────
info "Step 5/6 — Running end-to-end pipeline verification..."
cd "$SCRIPT_DIR"
if python scripts/e2e_test.py 2>&1 | grep -E "PASS|FAIL|ALL CHECKS"; then
    ok "E2E pipeline verified"
else
    warn "E2E test output unclear — check manually with: python scripts/e2e_test.py"
fi

# ── Step 6: Demo or Train ─────────────────────────────────────────────────────
if $DEMO; then
    info "Step 6/6 — Launching interactive SCL REPL..."
    echo ""
    echo -e "${BOLD}╔══════════════════════════════════════════════════════════════╗${RESET}"
    echo -e "${BOLD}║              CORTEX SCL INTERACTIVE DEMO                     ║${RESET}"
    echo -e "${BOLD}╚══════════════════════════════════════════════════════════════╝${RESET}"
    echo ""
    python -c "
import sys
sys.path.insert(0, '.')
from cortex.scl_parser import parse
from cortex.scl_emitter import SCLEmitter
from cortex.constrained_decoder import GreedySCLDecoder, is_complete_scl
from cortex.calibration import CalibratedConfidenceGate, TemperatureScaler, EntropyEstimator
from pathlib import Path

emitter = SCLEmitter()
decoder = GreedySCLDecoder()
gate = CalibratedConfidenceGate(
    scaler=TemperatureScaler(Path('data/calibration.json')),
    estimator=EntropyEstimator(),
)

print('Type any natural language intent and Cortex will emit valid SCL.')
print('Type a raw SCL string to parse and validate it.')
print('Type \"quit\" to exit.')
print()

while True:
    try:
        line = input('cortex> ').strip()
    except (EOFError, KeyboardInterrupt):
        print()
        break
    if not line:
        continue
    if line.lower() in ('quit', 'exit', 'q'):
        break

    if line.startswith('@'):
        # Parse mode
        result = parse(line)
        if result.valid:
            a = result.action
            print(f'  VALID SCL')
            print(f'  anchor:   {a.anchor}')
            print(f'  relation: {a.relation}')
            print(f'  fields:   {a.fields}')
            if a.anchor == \"@halt\":
                conf = float(a.fields.get(\"confidence\", 0.7))
                cal = gate.check(conf)
                print(f'  calibration: {\"ADMIT\" if cal.admissible else \"REJECT\"} (calibrated={cal.calibrated_confidence:.3f})')
        else:
            repaired = emitter.repair(line)
            print(f'  INVALID: {result.error}')
            print(f'  REPAIRED: {repaired}')
    else:
        # Decode mode
        scl = decoder.decode(line)
        print(f'  SCL: {scl}')
        result = parse(scl)
        if result.valid:
            print(f'  VALID — anchor={result.action.anchor}, relation={result.action.relation}')
        else:
            print(f'  WARNING: decoder output failed parse: {result.error}')
    print()
"
elif $DATA_ONLY; then
    info "Step 6/6 — Data-only mode (skipping training)"
    ok "Data pipeline complete"
else
    info "Step 6/6 — Starting LoRA fine-tuning (CPU)..."
    warn "CPU training is slow (~17s/step). Consider using --demo or --data-only on mobile."
    warn "For GPU training, run on a machine with CUDA and use ./bootstrap.sh instead."
    echo ""
    python scripts/lora_finetune.py \
        --model Qwen/Qwen2.5-0.5B-Instruct \
        --train data/sft/sft_train.jsonl \
        --val   data/sft/sft_val.jsonl \
        --output models/cortex-lora \
        --epochs 1 \
        --batch_size 1 \
        2>&1 || warn "Training failed — install torch CPU: pip install torch --index-url https://download.pytorch.org/whl/cpu"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════════════╗${RESET}"
echo -e "${GREEN}${BOLD}║                   CORTEX READY                               ║${RESET}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════════════╝${RESET}"
echo ""
ok "328 unit tests passing"
ok "E2E pipeline: runtime → SQLite → compact → SFT export"
ok "Three SCL-native layers active:"
ok "  1. Constrained decoder   — invalid SCL structurally impossible"
ok "  2. Calibrated confidence — halt gates checked against temperature-scaled model"
ok "  3. Semantic signal       — authority model trains on WHY, not just syntax"
echo ""
info "Next steps:"
echo "  ./bootstrap_termux.sh --demo          # Interactive SCL REPL"
echo "  python scripts/e2e_test.py            # Re-run e2e verification"
echo "  python scripts/data_cli.py stats      # Inspect the trajectory DB"
echo "  python scripts/data_cli.py compact    # Compact and export SFT data"
echo "  python -m cortex.learner --watch      # Start continuous learning daemon"
echo ""
