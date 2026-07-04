#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

MODEL_PATH="${ORNITH_MODEL_PATH:-$HOME/models/ornith/ornith-1.0-9b-Q4_K_M.gguf}"
LLAMA_SERVER="${LLAMA_SERVER:-$HOME/llama.cpp/build/bin/llama-server}"
HOST="${ORNITH_HOST:-127.0.0.1}"
PORT="${ORNITH_PORT:-8080}"
CTX="${ORNITH_CTX:-2048}"
THREADS="${ORNITH_THREADS:-4}"
REASONING_BUDGET="${ORNITH_REASONING_BUDGET:-128}"
LOG_DIR="${ORNITH_LOG_DIR:-$HOME/tmp}"
LOG_FILE="$LOG_DIR/ornith-llama-server.log"
PID_FILE="$LOG_DIR/ornith-llama-server.pid"

mkdir -p "$LOG_DIR"

SCRIPT_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"

quote_arg() {
  printf '%q' "$1"
}

launch_in_new_termux_terminal() {
  local launcher command_line arg value
  launcher="$LOG_DIR/start-pi-ornith-local-terminal.sh"

  command_line="cd $(quote_arg "$PWD") && export ORNITH_TERMINAL_LAUNCHED=1"
  for arg in ORNITH_MODEL_PATH LLAMA_SERVER ORNITH_HOST ORNITH_PORT ORNITH_CTX ORNITH_THREADS ORNITH_REASONING_BUDGET ORNITH_LOG_DIR; do
    if [ "${!arg+x}" = x ]; then
      value="${!arg}"
      command_line+=" && export $arg=$(quote_arg "$value")"
    fi
  done
  command_line+=" && exec $(quote_arg "$SCRIPT_PATH")"
  for arg in "$@"; do
    command_line+=" $(quote_arg "$arg")"
  done

  cat > "$launcher" <<EOF
#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
$command_line
EOF
  chmod 700 "$launcher"

  if am startservice \
    -n com.termux/.app.RunCommandService \
    -a com.termux.RUN_COMMAND \
    --es com.termux.RUN_COMMAND_PATH /data/data/com.termux/files/usr/bin/bash \
    --esa com.termux.RUN_COMMAND_ARGUMENTS "$launcher" \
    --es com.termux.RUN_COMMAND_WORKDIR "$PWD" \
    --ez com.termux.RUN_COMMAND_BACKGROUND false >/dev/null 2>&1 || \
    am start-foreground-service \
    -n com.termux/.app.RunCommandService \
    -a com.termux.RUN_COMMAND \
    --es com.termux.RUN_COMMAND_PATH /data/data/com.termux/files/usr/bin/bash \
    --esa com.termux.RUN_COMMAND_ARGUMENTS "$launcher" \
    --es com.termux.RUN_COMMAND_WORKDIR "$PWD" \
    --ez com.termux.RUN_COMMAND_BACKGROUND false >/dev/null 2>&1; then
    echo "Opened Pi in a new Termux terminal."
    exit 0
  fi

  if am start -n com.termux/.app.TermuxActivity >/dev/null 2>&1; then
    echo "Opened Termux, but Android refused the RUN_COMMAND intent." >&2
    echo "Enable Termux run-command support, then run: $SCRIPT_PATH" >&2
  else
    echo "No interactive terminal is attached, and a new Termux terminal could not be opened." >&2
  fi
  exit 1
}

ensure_interactive_terminal() {
  if [ -t 0 ] && [ -t 1 ]; then
    return 0
  fi

  if [ -r /dev/tty ] && [ -w /dev/tty ] && [ "${ORNITH_ATTACHED_TTY:-0}" != 1 ]; then
    exec env ORNITH_ATTACHED_TTY=1 "$SCRIPT_PATH" "$@" </dev/tty >/dev/tty 2>&1
  fi

  if [ "${ORNITH_TERMINAL_LAUNCHED:-0}" != 1 ]; then
    launch_in_new_termux_terminal "$@"
  fi

  echo "Pi needs an interactive terminal, but none is attached." >&2
  exit 1
}

ensure_interactive_terminal "$@"

if [ ! -x "$LLAMA_SERVER" ]; then
  echo "llama-server not found/executable: $LLAMA_SERVER" >&2
  exit 1
fi

if [ ! -f "$MODEL_PATH" ]; then
  echo "Ornith GGUF model not found: $MODEL_PATH" >&2
  exit 1
fi

server_alive() {
  curl -fsS "http://$HOST:$PORT/v1/models" >/dev/null 2>&1
}

if server_alive; then
  echo "Ornith llama.cpp server already running at http://$HOST:$PORT/v1"
else
  if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "Existing PID file found but API not ready; stopping PID $(cat "$PID_FILE")"
    kill "$(cat "$PID_FILE")" 2>/dev/null || true
    sleep 2
  fi

  echo "Starting Ornith local server..."
  nohup "$LLAMA_SERVER" \
    -m "$MODEL_PATH" \
    -c "$CTX" \
    -t "$THREADS" \
    -np 1 \
    --reasoning auto \
    --reasoning-budget "$REASONING_BUDGET" \
    --reasoning-format deepseek \
    --host "$HOST" \
    --port "$PORT" \
    > "$LOG_FILE" 2>&1 &
  echo $! > "$PID_FILE"

  echo "Waiting for server readiness..."
  for i in $(seq 1 90); do
    if server_alive; then
      break
    fi
    if ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "llama-server exited early. Last log lines:" >&2
      tail -80 "$LOG_FILE" >&2 || true
      exit 1
    fi
    sleep 1
  done

  if ! server_alive; then
    echo "Timed out waiting for Ornith API. Last log lines:" >&2
    tail -80 "$LOG_FILE" >&2 || true
    exit 1
  fi
fi

SYSTEM_PROMPT=$(cat <<'PROMPT'
You are Ornith running fully local inside Pi on Android/Termux. Be concise, practical, and action-oriented. Reason as needed, but avoid long visible reasoning. Prefer small verified steps.

You are working with Cortex. Cortex uses SCL: Semantic Compression Language. SCL is the canonical action/control language for world-affecting intent.

SCL shape:
@anchor → relation [key: value, key2: value2]

Core anchors and relations:
@state → update | snapshot
@memory → read | write | compress | ignore
@budget → spend | check | snapshot
@verify → run | assert
@tool → call | deny
@repair → rollback | patch | diagnose
@halt → answer | fail | defer

Examples:
@tool → call [name: "pytest", args: "tests/", risk: "verify"]
@memory → write [key: "rule.budget", value: "debit before execute", ttl: "persistent"]
@halt → answer [status: "complete", confidence: 0.91, evidence: "tests passed"]

Important Cortex rule: prose can explain, but executable/world-affecting intent should be expressible as one valid SCL control record. When asked to operate in Cortex/SCL mode, emit exactly one SCL record per step, then a brief human-readable note only if useful.

For normal Pi coding work, use Pi tools when available. Do not invent tool results. Read before editing. Prefer tests/verification after changes. If uncertain, say what evidence is missing.
PROMPT
)

echo "Starting Pi with ornith-local/ornith..."
echo "Server log: $LOG_FILE"

exec pi --offline \
  --provider ornith-local \
  --model ornith \
  --thinking low \
  --no-context-files \
  --system-prompt "$SYSTEM_PROMPT" \
  "$@"
