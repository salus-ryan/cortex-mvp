"""Resolve @{path} file mentions into bounded read-only context."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

MENTION_RE = re.compile(r"@\{([^{}\n\r]{1,240})\}")
MAX_FILES = 5
MAX_BYTES_PER_FILE = 20_000


def resolve_file_mentions(root: Path | str, text: str) -> dict[str, Any]:
    """Return safe file contexts for @{relative/path} mentions in *text*.

    Mentions are read-only, confined to *root*, and bounded so users can point the
    oracle at explicitly named files without granting execution authority.
    """
    base = Path(root).resolve()
    seen: set[str] = set()
    refs: list[dict[str, Any]] = []
    for raw in MENTION_RE.findall(text or ""):
        requested = raw.strip()
        if not requested or requested in seen:
            continue
        seen.add(requested)
        if len(refs) >= MAX_FILES:
            refs.append({"requested": requested, "status": "skipped", "reason": f"max {MAX_FILES} files per request"})
            continue
        refs.append(_read_one(base, requested))
    return {"status": "ok", "mentions": refs}


def enrich_text_with_file_mentions(root: Path | str, text: str) -> tuple[str, dict[str, Any]]:
    """Append readable @{file} contents to text and return metadata."""
    meta = resolve_file_mentions(root, text)
    available = [m for m in meta["mentions"] if m.get("status") == "included"]
    if not available:
        return text, meta
    blocks = ["Referenced files supplied by explicit @{file} mentions (read-only context):"]
    for item in available:
        truncated = "\n[truncated]" if item.get("truncated") else ""
        blocks.append(f"--- {item['path']} ---\n{item['content']}{truncated}")
    return text.rstrip() + "\n\n" + "\n\n".join(blocks), meta


def _read_one(base: Path, requested: str) -> dict[str, Any]:
    if "\x00" in requested:
        return {"requested": requested, "status": "refused", "reason": "nul byte in path"}
    candidate = (base / requested).resolve() if not Path(requested).is_absolute() else Path(requested).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        return {"requested": requested, "status": "refused", "reason": "path escapes workspace"}
    rel = str(candidate.relative_to(base))
    if not candidate.exists():
        return {"requested": requested, "path": rel, "status": "missing", "reason": "file not found"}
    if not candidate.is_file():
        return {"requested": requested, "path": rel, "status": "refused", "reason": "not a regular file"}
    data = candidate.read_bytes()[: MAX_BYTES_PER_FILE + 1]
    truncated = len(data) > MAX_BYTES_PER_FILE
    if truncated:
        data = data[:MAX_BYTES_PER_FILE]
    content = data.decode("utf-8", errors="replace")
    return {
        "requested": requested,
        "path": rel,
        "status": "included",
        "bytes": candidate.stat().st_size,
        "truncated": truncated,
        "content": content,
    }
