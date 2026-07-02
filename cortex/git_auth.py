"""Lawful Git credential detection for Cortex.

This module intentionally does not obtain secrets. It detects already-granted
authority and explains the least-privilege ways an operator can grant access.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

SECRET_ENV_NAMES = ("GITHUB_TOKEN", "GH_TOKEN")


@dataclass
class GitAuthStatus:
    remote: str | None
    can_fetch: bool
    can_push_dry_run: bool
    auth_sources: list[str]
    safe_next_steps: list[str]
    errors: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)


def detect_git_auth(root: Path | str = ".") -> GitAuthStatus:
    root = Path(root).resolve()
    errors: list[str] = []
    sources: list[str] = []

    remote: str | None = None
    r = _run(["git", "remote", "get-url", "origin"], root)
    if r.returncode == 0:
        remote = r.stdout.strip()
    else:
        errors.append((r.stderr or r.stdout).strip())

    for name in SECRET_ENV_NAMES:
        if os.environ.get(name):
            sources.append(f"environment:{name}")

    ssh = _run(["ssh", "-T", "git@github.com"], root)
    if "successfully authenticated" in (ssh.stdout + ssh.stderr).lower():
        sources.append("ssh:github")

    gh = _run(["gh", "auth", "status"], root) if _has_cmd("gh") else None
    if gh and gh.returncode == 0:
        sources.append("github-cli")

    fetch = _run(["git", "fetch", "--dry-run", "origin"], root)
    can_fetch = fetch.returncode == 0
    if not can_fetch:
        errors.append((fetch.stderr or fetch.stdout).strip())

    push = _run(["git", "push", "--dry-run", "origin", "HEAD"], root)
    can_push = push.returncode == 0
    if not can_push:
        errors.append((push.stderr or push.stdout).strip())

    next_steps = [
        "Install a fine-grained GitHub token as GITHUB_TOKEN/GH_TOKEN for this process only.",
        "Or configure an SSH deploy key scoped to this repository.",
        "Or run `gh auth login` interactively as the authorized account.",
        "Then run `git push origin HEAD`.",
    ]
    return GitAuthStatus(remote, can_fetch, can_push, sources, next_steps, [e for e in errors if e])


def _has_cmd(name: str) -> bool:
    return subprocess.run(["sh", "-c", f"command -v {name} >/dev/null 2>&1"]).returncode == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cortex-git-auth")
    parser.add_argument("--root", default=".")
    args = parser.parse_args(argv)
    import json
    print(json.dumps(detect_git_auth(args.root).to_dict(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
