#!/usr/bin/env python3
"""Tiny no-browser mobile E2E runner for Cortex.

Why this exists: Playwright/Puppeteer are not available on Android/Termux in this
harness. This runner tests the same contract the mobile app depends on:
static PWA assets, live PID1/awareness/auth APIs, and protected endpoint auth.

Usage:
  scripts/mobile_e2e.py https://cortex-pid1-production.up.railway.app

With token:
  CORTEX_AUTH_TOKEN=... scripts/mobile_e2e.py https://...
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass
class Check:
    name: str
    passed: bool
    detail: dict[str, Any]


class MobileE2E:
    def __init__(self, base: str, token: str | None = None) -> None:
        self.base = base.rstrip("/")
        self.token = token
        self.checks: list[Check] = []

    def headers(self, path: str = "", method: str = "GET", json_body: bool = False, auth: bool = False) -> dict[str, str]:
        h: dict[str, str] = {"user-agent": "cortex-mobile-e2e"}
        if json_body:
            h["content-type"] = "application/json"
        if auth and self.token:
            h["authorization"] = "Bearer " + self.token
            h["x-cortex-actor"] = "mobile-e2e"
            cap = self.capability_for_path(path)
            if cap and method.upper() != "GET":
                ts = str(int(time.time()))
                intent = json.dumps({"path": path, "method": method.upper(), "actor": "mobile-e2e", "at": ts}, separators=(",", ":"))
                msg = f"{ts}.{path}.{cap}.{intent}".encode()
                h["x-cortex-intent-timestamp"] = ts
                h["x-cortex-intent"] = intent
                h["x-cortex-intent-signature"] = hmac.new(self.token.encode(), msg, hashlib.sha256).hexdigest()
        return h

    def capability_for_path(self, path: str) -> str | None:
        return {
            "/memory/write": "memory:write",
            "/tool/execute": "tool:execute",
            "/patch/apply": "patch:apply",
            "/build/apply": "build:apply",
            "/deploy/check": "deploy:execute",
            "/deploy/railway": "deploy:execute",
            "/deploy/forge": "deploy:execute",
            "/payments/checkout": "payments:checkout",
            "/immune/quarantine": "immune:quarantine",
            "/self-train/collect": "self_train:execute",
            "/self-train/eval": "self_train:execute",
        }.get(path)

    def request(self, path: str, method: str = "GET", payload: dict[str, Any] | None = None, auth: bool = False) -> tuple[int, str, dict[str, str]]:
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(self.base + path, data=data, headers=self.headers(path, method, payload is not None, auth), method=method)
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode(), dict(resp.headers)

    def expect(self, name: str, passed: bool, **detail: Any) -> None:
        self.checks.append(Check(name, passed, detail))

    def run(self) -> dict[str, Any]:
        self.check_mobile_assets()
        self.check_live_state()
        self.check_auth_boundary()
        if self.token:
            self.check_token_flow()
        return self.report()

    def check_mobile_assets(self) -> None:
        code, html, headers = self.request("/mobile")
        self.expect("mobile_html", code == 200 and "mobile witness console" in html and "localStorage.cortexToken" in html, code=code, has_console="mobile witness console" in html, has_token_storage="localStorage.cortexToken" in html, content_type=headers.get("content-type"))
        code, body, _ = self.request("/mobile/manifest.json")
        manifest = json.loads(body)
        self.expect("manifest", code == 200 and manifest.get("name") == "Cortex" and manifest.get("display") == "standalone", code=code, app_name=manifest.get("name"), display=manifest.get("display"))
        code, sw, _ = self.request("/mobile/service-worker.js")
        self.expect("service_worker", code == 200 and "cortex-mobile" in sw and "fetch" in sw, code=code, has_cache="cortex-mobile" in sw, has_fetch="fetch" in sw)

    def check_live_state(self) -> None:
        code, body, _ = self.request("/pid1")
        pid1 = json.loads(body)
        self.expect("pid1", code == 200 and pid1.get("pid") == 1 and pid1.get("is_pid1") is True, code=code, pid=pid1.get("pid"), is_pid1=pid1.get("is_pid1"), children=len(pid1.get("children", {})))
        code, body, _ = self.request("/awareness")
        aware = json.loads(body)
        self.expect("awareness", code == 200 and aware.get("status") == "aware" and aware.get("self_model", {}).get("is_pid1") is True, code=code, status=aware.get("status"), is_pid1=aware.get("self_model", {}).get("is_pid1"))
        code, body, _ = self.request("/auth/status")
        auth = json.loads(body)
        self.expect("auth_status", code == 200 and "protected_paths" in auth, code=code, configured=auth.get("configured"), mode=auth.get("mode"), protected_paths=len(auth.get("protected_paths", {})), rate_limit=auth.get("rate_limit"), signed_intents_required=auth.get("signed_intents_required"))

    def check_auth_boundary(self) -> None:
        try:
            self.request("/deploy/check", "POST", {}, auth=False)
        except urllib.error.HTTPError as exc:
            body = json.loads(exc.read().decode())
            self.expect("anonymous_protected_rejected", exc.code == 401 and body.get("status") == "unauthorized", code=exc.code, reason=body.get("reason"), capability=body.get("capability"))
            return
        self.expect("anonymous_protected_rejected", False, reason="protected endpoint accepted anonymous request")

    def check_token_flow(self) -> None:
        code, body, _ = self.request("/auth/me", auth=True)
        me = json.loads(body)
        self.expect("token_auth_me", code == 200 and me.get("allowed") is True, code=code, status=me.get("status"), allowed=me.get("allowed"), actor=me.get("actor"))
        code, body, _ = self.request("/deploy/check", "POST", {}, auth=True)
        deploy = json.loads(body)
        self.expect("token_deploy_check", code == 200 and deploy.get("may_execute") is False and deploy.get("status") in {"pass", "blocked"}, code=code, status=deploy.get("status"), may_execute=deploy.get("may_execute"))

    def report(self) -> dict[str, Any]:
        return {
            "status": "pass" if all(c.passed for c in self.checks) else "fail",
            "base": self.base,
            "token_tested": bool(self.token),
            "checks": [{"name": c.name, "passed": c.passed, **c.detail} for c in self.checks],
        }


def main(argv: list[str]) -> int:
    base = argv[1] if len(argv) > 1 else os.environ.get("CORTEX_BASE", "https://cortex-pid1-production.up.railway.app")
    token = os.environ.get("CORTEX_AUTH_TOKEN")
    report = MobileE2E(base, token).run()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
