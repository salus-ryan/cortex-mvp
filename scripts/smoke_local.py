#!/usr/bin/env python3
"""Smoke-test a running local Cortex web service.

Usage:
    python -m cortex.web
    python scripts/smoke_local.py http://127.0.0.1:8000
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")


def get(path: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(BASE + path, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def post(path: str, body: dict) -> tuple[int, str]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, headers={"content-type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def require(ok: bool, message: str) -> None:
    if not ok:
        raise SystemExit(f"FAIL: {message}")
    print(f"OK: {message}")


def main() -> None:
    checks = ["/health", "/oauth/status", "/foundry/repos", "/foundry/plan"]
    for path in checks:
        code, text = get(path)
        require(code == 200, f"GET {path} returned 200")
        require(text.strip().startswith("{"), f"GET {path} returned JSON-ish body")

    code, text = post(
        "/v1/chat/completions",
        {"model": "cortex-local-mind-v1", "messages": [{"role": "user", "content": "Say hello from Cortex smoke test."}]},
    )
    require(code == 200, "POST /v1/chat/completions returned 200")
    data = json.loads(text)
    require("choices" in data, "chat completion has choices")
    print("\nSmoke test passed:", BASE)


if __name__ == "__main__":
    main()
