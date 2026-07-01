#!/usr/bin/env python3
"""
Cortex Chat — plain language in, plain language out.
SCL governance runs invisibly underneath.
"""
import sys
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from cortex.scl_emitter import SCLEmitter
from cortex.scl_parser import SCLAction
from cortex.policy import Policy
from cortex.verifier import Verifier
from cortex.budget import Budget
from cortex.tool_registry import ToolRegistry, ToolSpec
from cortex.store import TrajectoryStore
from cortex.calibration import CalibratedConfidenceGate

# ---------------------------------------------------------------------------
# Build a tool registry with the tools chat.py actually supports
# ---------------------------------------------------------------------------

def _make_registry() -> ToolRegistry:
    tr = ToolRegistry()
    tr.register(ToolSpec(name="bash",   description="Run a shell command",    risk_tier="write_limited", unit_cost=2))
    tr.register(ToolSpec(name="search", description="Search the web",          risk_tier="read_only",     unit_cost=1))
    tr.register(ToolSpec(name="read",   description="Read a file",             risk_tier="read_only",     unit_cost=1))
    tr.register(ToolSpec(name="write",  description="Write a file",            risk_tier="write_limited", unit_cost=2))
    return tr

# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _tool_bash(args: str) -> str:
    try:
        r = subprocess.run(args, shell=True, capture_output=True, text=True, timeout=10)
        out = (r.stdout + r.stderr).strip()
        return out[:2000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "(command timed out after 10s)"
    except Exception as e:
        return f"(error: {e})"

def _tool_search(query: str) -> str:
    return f"(search not connected — query was: '{query}')"

def _tool_read(path: str) -> str:
    try:
        return Path(path.strip()).read_text()[:2000]
    except Exception as e:
        return f"(could not read '{path}': {e})"

def _tool_write(args: str) -> str:
    if "::" in args:
        path, content = args.split("::", 1)
        try:
            Path(path.strip()).write_text(content)
            return f"Written to {path.strip()}"
        except Exception as e:
            return f"(could not write: {e})"
    return "(usage: path::content)"

TOOL_FNS = {
    "bash":   _tool_bash,
    "search": _tool_search,
    "read":   _tool_read,
    "write":  _tool_write,
}

# ---------------------------------------------------------------------------
# NL → SCL  (the invisible translation layer)
# ---------------------------------------------------------------------------

def nl_to_scl(text: str) -> SCLAction:
    """Convert natural language to a SCLAction with correct risk tiers."""
    t = text.lower().strip()

    # Already SCL — parse it
    if text.strip().startswith("@"):
        emitter = SCLEmitter()
        emit = emitter.parse_and_repair(text)
        if emit.valid and emit.action:
            return emit.action

    # --- File / shell ---
    if any(w in t for w in ["list files", "ls ", "show files", "what files", "show me files"]):
        path = "."
        for m in re.findall(r'in ([/\w.\-]+)', t):
            path = m
        return SCLAction(anchor="@tool", relation="call",
                         fields={"name": "bash", "args": f"ls -la {path}", "risk": "write_limited"},
                         raw=text)

    if any(w in t for w in ["run ", "execute ", "shell "]):
        cmd = re.sub(r'.*(run|execute|shell)\s+', '', text, flags=re.I).strip()
        return SCLAction(anchor="@tool", relation="call",
                         fields={"name": "bash", "args": cmd, "risk": "write_limited"},
                         raw=text)

    if any(w in t for w in ["read file", "show file", "open file", "cat "]):
        m = re.search(r'[\w/.\-]+\.\w+', text)
        path = m.group(0) if m else "."
        return SCLAction(anchor="@tool", relation="call",
                         fields={"name": "read", "args": path, "risk": "read_only"},
                         raw=text)

    if any(w in t for w in ["search for", "search ", "look up", "find info", "google"]):
        query = re.sub(r'.*(search for|search|look up|find info|google)\s+', '', text, flags=re.I).strip()
        return SCLAction(anchor="@tool", relation="call",
                         fields={"name": "search", "args": query, "risk": "read_only"},
                         raw=text)

    # --- Memory ---
    if any(w in t for w in ["remember ", "save ", "store "]):
        content = re.sub(r'.*(remember|save|store)\s+', '', text, flags=re.I).strip()
        return SCLAction(anchor="@memory", relation="write",
                         fields={"key": "note", "value": content, "tier": "episodic"},
                         raw=text)

    if any(w in t for w in ["recall ", "what did i", "do you remember", "remind me"]):
        key = re.sub(r'.*(recall|what did i|do you remember|remind me)\s+', '', text, flags=re.I).strip()
        return SCLAction(anchor="@memory", relation="read",
                         fields={"key": key, "tier": "episodic"},
                         raw=text)

    # --- Completion / failure ---
    if any(w in t for w in ["done", "finished", "complete", "all good", "that's it", "thats it"]):
        return SCLAction(anchor="@halt", relation="answer",
                         fields={"status": "complete", "confidence": "0.9", "evidence": text},
                         raw=text)

    if any(w in t for w in ["can't", "cannot", "unable", "don't know", "failed"]):
        return SCLAction(anchor="@halt", relation="fail",
                         fields={"status": "failed", "confidence": "0.8", "evidence": text},
                         raw=text)

    # --- Destructive intent — refuse before reaching bash ---
    _DESTRUCTIVE = ["delete everything", "rm -rf", "wipe ", "format ", "destroy ",
                    "nuke ", "erase everything", "delete all", "remove all"]
    if any(d in t for d in _DESTRUCTIVE):
        return SCLAction(anchor="@halt", relation="fail",
                         fields={"status": "denied", "confidence": "1.0",
                                 "evidence": "destructive intent detected — refused"},
                         raw=text)

    # --- Default: echo via bash ---
    safe = text.replace("'", "\\'")
    return SCLAction(anchor="@tool", relation="call",
                     fields={"name": "bash", "args": f"echo '{safe}'", "risk": "write_limited"},
                     raw=text)

# ---------------------------------------------------------------------------
# SCL → plain English response renderer
# ---------------------------------------------------------------------------

def render_response(action: SCLAction, tool_result: str | None = None) -> str:
    f = action.fields
    if action.anchor == "@halt":
        evidence = f.get("evidence", "")
        if action.relation == "answer":
            return tool_result or evidence or "Done."
        elif action.relation == "fail":
            return f"I wasn't able to do that. {evidence}"
        elif action.relation == "defer":
            return f"I need more information. {evidence}"
    if action.anchor == "@tool" and tool_result:
        return tool_result
    if action.anchor == "@memory":
        if action.relation == "read":
            return tool_result or f"(nothing found for '{f.get('key', '')}')"
        return f"Remembered: {f.get('value', '')}"
    if action.anchor == "@state":
        return f"State updated: {f.get('key', f.get('status', ''))}"
    return tool_result or f.get("evidence", "OK.")

# ---------------------------------------------------------------------------
# Chat loop
# ---------------------------------------------------------------------------

def chat_loop(db_path: str = "data/cortex.db", show_scl: bool = False):
    os.makedirs(Path(db_path).parent, exist_ok=True)

    store    = TrajectoryStore(db_path)
    policy   = Policy()
    verifier = Verifier()
    budget   = Budget(max_units=10000, max_steps=500)
    registry = _make_registry()
    calib    = CalibratedConfidenceGate()
    memory   = {}  # in-session key-value memory

    task_id  = store.start_task("chat-session", {"mode": "interactive"})
    step     = 0

    print()
    print("  Cortex  —  type anything. 'quit' to exit, 'debug' to toggle SCL view.")
    print()

    while True:
        try:
            user_input = input("  you: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Cortex: Goodbye.")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "bye"):
            print("  Cortex: Goodbye.")
            break
        if user_input.lower() == "debug":
            show_scl = not show_scl
            print(f"  [SCL view {'ON — you will see the governance layer' if show_scl else 'OFF'}]")
            continue

        # 1. NL → SCL
        action = nl_to_scl(user_input)

        if show_scl:
            fields_str = ", ".join(f"{k}: {v!r}" for k, v in action.fields.items())
            print(f"  [SCL] {action.anchor} → {action.relation} [{fields_str}]")

        # 2. Policy check
        pol = policy.check(action, budget, registry)
        if not pol.allowed:
            if show_scl:
                print(f"  [POLICY DENIED] {pol.reason}")
            print(f"  Cortex: I can't do that. ({pol.reason})")
            store.log_step(task_id, step, user_input,
                           f"{action.anchor} → {action.relation}", "denied", 0.0,
                           {"policy_reason": pol.reason})
            step += 1
            continue

        # 3. Verifier check
        ver = verifier.check_action(action, budget, registry)
        if not ver.passed:
            if show_scl:
                print(f"  [VERIFIER BLOCKED] {ver.reason}")
            print(f"  Cortex: I can't do that safely. ({ver.reason})")
            store.log_step(task_id, step, user_input,
                           f"{action.anchor} → {action.relation}", "blocked", 0.0,
                           {"verifier_reason": ver.reason})
            step += 1
            continue

        # 4. Execute
        tool_result = None

        if action.anchor == "@tool" and action.relation == "call":
            name = action.fields.get("name", "bash").strip('"')
            args = action.fields.get("args", "").strip('"')
            fn   = TOOL_FNS.get(name, _tool_bash)
            tool_result = fn(args)

        elif action.anchor == "@memory":
            key = action.fields.get("key", "").strip('"')
            if action.relation == "write":
                memory[key] = action.fields.get("value", "").strip('"')
                tool_result = f"Remembered: {memory[key]}"
            elif action.relation == "read":
                tool_result = memory.get(key, f"(nothing remembered for '{key}')")

        # 5. Calibration gate on halts
        if action.anchor == "@halt":
            conf = float(action.fields.get("confidence", 0.9))
            gate = calib.check(conf, {"outcome": "success"})
            if not gate.get("admit", True):
                tool_result = "(low confidence — please clarify)"

        # 6. Render
        response = render_response(action, tool_result)

        # 7. Log
        quality = 1.0 if action.anchor == "@halt" and action.relation == "answer" else 0.8
        store.log_step(task_id, step, user_input,
                       f"{action.anchor} → {action.relation}",
                       "success", quality, {"response": response})
        step += 1

        print(f"  Cortex: {response}")
        print()

        # New task context after halt
        if action.anchor == "@halt":
            store.finish_task(task_id, action.relation,
                              float(action.fields.get("confidence", 0.9)))
            task_id = store.start_task("chat-session", {"mode": "interactive"})
            step = 0


def main():
    import argparse
    p = argparse.ArgumentParser(description="Cortex Chat")
    p.add_argument("--db",      default="data/cortex.db")
    p.add_argument("--verbose", action="store_true", help="Show SCL traces")
    args = p.parse_args()
    chat_loop(db_path=args.db, show_scl=args.verbose)


if __name__ == "__main__":
    main()
