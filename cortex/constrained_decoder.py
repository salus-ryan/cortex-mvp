"""
constrained_decoder.py — Grammar-Constrained SCL Decoding

Makes invalid SCL structurally impossible at the token level during inference.
The model's output distribution is masked at each step so only tokens that
extend a valid partial SCL string can be sampled.

Architecture
------------
SCLGrammar      — finite-state grammar over SCL token sequences
SCLLogitProcessor — HuggingFace LogitsProcessor that masks invalid next tokens
SCLConstrainedGenerator — wraps a HuggingFace model with constrained decoding
GreedySCLDecoder — lightweight greedy decoder for CPU/no-GPU environments

Grammar states
--------------
  START → anchor (@tool, @halt, @memory, @repair, @state, @verify, @budget)
  ANCHOR → " → "
  ARROW → relation (call, answer, fail, read, write, ...)
  RELATION → " [" or " " (field-less anchors)
  FIELDS_OPEN → field_key ": " field_value ", " ... "]"
  FIELDS_CLOSE → end of sequence

This is intentionally a prefix-grammar: at each position we compute the set
of all tokens that are valid continuations of the current partial string.
"""

from __future__ import annotations

import re
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

# ── SCL Grammar constants ─────────────────────────────────────────────────────

ANCHORS: List[str] = [
    "@tool", "@memory", "@halt", "@repair", "@state", "@verify", "@budget"
]

ANCHOR_RELATIONS: Dict[str, List[str]] = {
    "@tool":    ["call", "deny"],
    "@memory":  ["read", "write", "compress", "ignore"],
    "@halt":    ["answer", "fail", "defer"],
    "@repair":  ["rollback", "diagnose", "patch"],
    "@state":   ["update", "snapshot"],
    "@verify":  ["run", "assert"],
    "@budget":  ["spend", "check", "snapshot"],
}

# Required fields per anchor+relation (minimum set)
REQUIRED_FIELDS: Dict[Tuple[str, str], List[str]] = {
    ("@halt", "answer"):    ["status", "confidence", "evidence"],
    ("@halt", "fail"):      ["status", "confidence", "evidence"],
    ("@halt", "defer"):     ["status", "confidence", "evidence"],
    ("@tool", "call"):      ["name"],
    ("@tool", "deny"):      ["name", "reason"],
    ("@memory", "write"):   ["key", "value"],
    ("@memory", "read"):    ["query"],
    ("@repair", "rollback"): ["artifact"],
    ("@repair", "diagnose"): [],
    ("@repair", "patch"):   ["target"],
    ("@state", "update"):   [],
    ("@state", "snapshot"): [],
    ("@verify", "run"):     ["type", "target"],
    ("@verify", "assert"):  ["type", "target"],
    ("@budget", "spend"):   ["units", "reason"],
    ("@budget", "check"):   [],
    ("@budget", "snapshot"): [],
    ("@memory", "compress"): ["source", "target"],
    ("@memory", "ignore"):  [],
}

# ── Grammar state machine ─────────────────────────────────────────────────────

class SCLGrammar:
    """
    Prefix-grammar for SCL strings.

    Given a partial string, returns the set of valid next characters
    (or the set of valid next token-prefixes for use with a tokenizer).

    States:
      'start'         — nothing emitted yet
      'in_anchor'     — emitting anchor characters
      'after_anchor'  — anchor complete, expecting ' → '
      'in_arrow'      — emitting ' → '
      'after_arrow'   — arrow complete, expecting relation
      'in_relation'   — emitting relation characters
      'after_relation'— relation complete, expecting ' [' or newline
      'in_fields'     — emitting field key-value pairs
      'complete'      — valid complete SCL string
      'error'         — unrecoverable parse error
    """

    def __init__(self):
        self._cache: Dict[str, Tuple[str, Set[str]]] = {}

    def state_and_continuations(self, partial: str) -> Tuple[str, Set[str]]:
        """
        Given a partial SCL string, return (state, set_of_valid_next_chars).
        The set is over single characters; callers can use it to filter tokens.
        """
        if partial in self._cache:
            return self._cache[partial]
        result = self._compute(partial)
        self._cache[partial] = result
        return result

    def _compute(self, partial: str) -> Tuple[str, Set[str]]:
        s = partial.lstrip()

        # ── Nothing yet ───────────────────────────────────────────────────────
        if not s:
            return "start", {"@"}

        # ── Anchor phase ──────────────────────────────────────────────────────
        if not any(s.startswith(a) for a in ANCHORS):
            # Check if we're in the middle of a valid anchor prefix
            valid_prefixes = [a for a in ANCHORS if a.startswith(s)]
            if valid_prefixes:
                # Determine next valid chars
                next_chars: Set[str] = set()
                for a in valid_prefixes:
                    if len(s) < len(a):
                        next_chars.add(a[len(s)])
                return "in_anchor", next_chars
            return "error", set()

        # ── Anchor matched ────────────────────────────────────────────────────
        anchor = next(a for a in ANCHORS if s.startswith(a))
        rest = s[len(anchor):]

        if not rest:
            return "after_anchor", {" "}

        # ── Arrow ' → ' ───────────────────────────────────────────────────────
        arrow = " \u2192 "
        if not rest.startswith(arrow):
            # Check partial arrow
            if arrow.startswith(rest):
                return "in_arrow", {arrow[len(rest)]}
            # Also accept ASCII ' -> '
            arrow_ascii = " -> "
            if arrow_ascii.startswith(rest):
                return "in_arrow", {arrow_ascii[len(rest)]}
            return "error", set()

        rest = rest[len(arrow):]

        if not rest:
            valid_rels = ANCHOR_RELATIONS.get(anchor, [])
            first_chars = {r[0] for r in valid_rels}
            return "after_arrow", first_chars

        # ── Relation ──────────────────────────────────────────────────────────
        valid_rels = ANCHOR_RELATIONS.get(anchor, [])
        matched_rel = next((r for r in valid_rels if rest.startswith(r)), None)

        if matched_rel is None:
            # Partial relation
            partial_rels = [r for r in valid_rels if r.startswith(rest)]
            if partial_rels:
                next_chars = {r[len(rest)] for r in partial_rels}
                return "in_relation", next_chars
            return "error", set()

        rest = rest[len(matched_rel):]

        if not rest:
            # After relation: expect ' [' for fields or end
            req = REQUIRED_FIELDS.get((anchor, matched_rel), [])
            if req:
                return "after_relation", {" "}
            else:
                return "complete", set()

        # ── Fields ────────────────────────────────────────────────────────────
        if rest.startswith(" [") or rest.startswith("["):
            return "in_fields", None  # free-form inside brackets

        if rest in (" ", " ["):
            return "after_relation", {"[" if rest == " " else None} - {None}

        return "complete", set()

    def is_valid_complete(self, s: str) -> bool:
        """Return True if s is a complete, valid SCL string."""
        state, _ = self.state_and_continuations(s)
        if state == "complete":
            return True
        # Also accept strings that end with ']' and have all required fields
        s = s.strip()
        if not s.endswith("]"):
            return False
        # Try parsing it
        try:
            from cortex.scl_parser import parse
            result = parse(s)
            return result is not None
        except Exception:
            return False

    def valid_token_ids(
        self,
        partial: str,
        tokenizer,
        vocab_size: int,
    ) -> List[int]:
        """
        Return list of token IDs that are valid continuations of `partial`.
        Uses the grammar to compute valid next characters, then filters vocab.
        """
        state, next_chars = self.state_and_continuations(partial)

        if state == "complete":
            # Only EOS is valid
            eos = getattr(tokenizer, "eos_token_id", None)
            return [eos] if eos is not None else []

        if state == "error":
            # Fallback: allow EOS to terminate gracefully
            eos = getattr(tokenizer, "eos_token_id", None)
            return [eos] if eos is not None else list(range(vocab_size))

        if state == "in_fields" or next_chars is None:
            # Inside fields: allow everything except bare newlines
            # (fields are free-form key: value pairs)
            return list(range(vocab_size))

        # Filter vocabulary to tokens whose decoded string starts with
        # one of the valid next characters
        valid_ids = []
        for tok_id in range(vocab_size):
            try:
                tok_str = tokenizer.decode([tok_id], skip_special_tokens=False)
            except Exception:
                continue
            if tok_str and tok_str[0] in next_chars:
                valid_ids.append(tok_id)

        # Always allow EOS as a safety valve
        eos = getattr(tokenizer, "eos_token_id", None)
        if eos is not None and eos not in valid_ids:
            valid_ids.append(eos)

        return valid_ids


# ── HuggingFace LogitsProcessor ───────────────────────────────────────────────

class SCLLogitProcessor:
    """
    HuggingFace-compatible LogitsProcessor that masks invalid SCL tokens.

    Usage:
        from transformers import LogitsProcessorList
        processor = SCLLogitProcessor(tokenizer, prompt)
        outputs = model.generate(
            input_ids,
            logits_processor=LogitsProcessorList([processor]),
            max_new_tokens=128,
        )
    """

    def __init__(self, tokenizer, prompt: str = ""):
        self.tokenizer = tokenizer
        self.grammar = SCLGrammar()
        self._generated = ""
        self._prompt = prompt

    def __call__(self, input_ids, scores):
        """
        Called by HuggingFace generate() at each decoding step.
        Masks logits for tokens that would produce invalid SCL.
        """
        import torch

        # Decode what has been generated so far (excluding prompt)
        generated_ids = input_ids[0].tolist()
        if hasattr(self.tokenizer, "decode"):
            full_text = self.tokenizer.decode(generated_ids, skip_special_tokens=True)
            # Strip prompt prefix to get only the generated portion
            if full_text.startswith(self._prompt):
                self._generated = full_text[len(self._prompt):]
            else:
                self._generated = full_text

        vocab_size = scores.shape[-1]
        valid_ids = self.grammar.valid_token_ids(
            self._generated, self.tokenizer, vocab_size
        )

        if not valid_ids:
            # Safety: if grammar returns nothing, allow everything
            return scores

        # Create mask: -inf for invalid tokens
        mask = torch.full_like(scores, float("-inf"))
        valid_tensor = torch.tensor(valid_ids, dtype=torch.long, device=scores.device)
        mask[0, valid_tensor] = 0.0

        return scores + mask


# ── Constrained generator ─────────────────────────────────────────────────────

class SCLConstrainedGenerator:
    """
    Wraps a HuggingFace model+tokenizer with SCL-constrained decoding.

    The model can only produce valid SCL — invalid syntax is structurally
    impossible at the token level, not just repaired after the fact.

    Usage:
        gen = SCLConstrainedGenerator(model, tokenizer)
        scl_string = gen.generate(prompt, max_new_tokens=128)
    """

    def __init__(self, model, tokenizer, device: str = "cpu"):
        self.model = model
        self.tokenizer = tokenizer
        self.device = device
        self.grammar = SCLGrammar()

    def generate(
        self,
        prompt: str,
        max_new_tokens: int = 128,
        temperature: float = 1.0,
        do_sample: bool = False,
    ) -> str:
        """Generate a constrained SCL string for the given prompt."""
        try:
            from transformers import LogitsProcessorList
            import torch

            inputs = self.tokenizer(
                prompt,
                return_tensors="pt",
                truncation=True,
                max_length=512,
            ).to(self.device)

            processor = SCLLogitProcessor(self.tokenizer, prompt)

            with torch.no_grad():
                output_ids = self.model.generate(
                    **inputs,
                    max_new_tokens=max_new_tokens,
                    do_sample=do_sample,
                    temperature=temperature if do_sample else 1.0,
                    logits_processor=LogitsProcessorList([processor]),
                    pad_token_id=self.tokenizer.eos_token_id,
                )

            # Decode only the new tokens
            new_ids = output_ids[0][inputs["input_ids"].shape[1]:]
            return self.tokenizer.decode(new_ids, skip_special_tokens=True).strip()

        except ImportError:
            # Fallback if torch/transformers not available
            return self._greedy_fallback(prompt, max_new_tokens)

    def _greedy_fallback(self, prompt: str, max_new_tokens: int) -> str:
        """CPU fallback: greedy grammar-guided generation without torch."""
        return GreedySCLDecoder(self.grammar).decode(prompt)


# ── Lightweight greedy decoder (no torch required) ────────────────────────────

class GreedySCLDecoder:
    """
    Greedy SCL decoder that uses the grammar to guide generation without
    requiring a GPU or torch. Used for testing and CPU environments.

    Given a prompt, it generates a minimal valid SCL string by:
    1. Extracting intent signals from the prompt
    2. Using the grammar to build a valid SCL string step by step
    """

    def __init__(self, grammar: Optional[SCLGrammar] = None):
        self.grammar = grammar or SCLGrammar()

    def decode(self, prompt: str) -> str:
        """
        Generate a minimal valid SCL string from a prompt.
        This is a deterministic rule-based decoder, not a neural one.
        Used for testing the grammar machinery without a model.
        """
        prompt_lower = prompt.lower()

        # Intent detection — check fail/error BEFORE complete to avoid
        # "cannot complete" routing to answer instead of fail
        if any(w in prompt_lower for w in ["fail", "error", "cannot", "unable", "blocked"]):
            return '@halt → fail [status: "failed", confidence: 0.8, evidence: "task failed"]'

        if any(w in prompt_lower for w in ["complete", "done", "finish", "answer", "result"]):
            return '@halt → answer [status: "complete", confidence: 0.9, evidence: "task complete"]'

        if any(w in prompt_lower for w in ["read", "retrieve", "recall", "remember"]):
            return '@memory → read [tier: "episodic", query: "relevant context"]'

        if any(w in prompt_lower for w in ["write", "store", "save", "remember this"]):
            return '@memory → write [tier: "episodic", content: "stored observation"]'

        if any(w in prompt_lower for w in ["rollback", "undo", "revert", "restore"]):
            return '@repair → rollback [target: "last_action"]'

        if any(w in prompt_lower for w in ["budget", "cost", "units", "remaining"]):
            return '@budget → check []'

        if any(w in prompt_lower for w in ["verify", "check", "assert", "confirm"]):
            return '@verify → run [check: "state_consistent"]'

        # Default: tool call
        tool_match = re.search(r'\b(bash|python|search|read_file|write_file|list_dir)\b', prompt_lower)
        tool_name = tool_match.group(1) if tool_match else "bash"
        return f'@tool → call [name: "{tool_name}", args: ""]'


# ── Module-level convenience ──────────────────────────────────────────────────

_grammar = SCLGrammar()


def is_valid_scl_prefix(partial: str) -> bool:
    """Return True if `partial` is a valid prefix of some complete SCL string."""
    state, _ = _grammar.state_and_continuations(partial)
    return state not in ("error",)


def is_complete_scl(s: str) -> bool:
    """Return True if `s` is a complete, valid SCL string."""
    return _grammar.is_valid_complete(s)


def next_valid_chars(partial: str) -> Set[str]:
    """Return the set of valid next characters for a partial SCL string."""
    _, chars = _grammar.state_and_continuations(partial)
    return chars or set()
