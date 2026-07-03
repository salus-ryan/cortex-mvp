"""GitHub Actions provider for no-VPS Forge execution."""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass
class GitHubActionsProvider:
    owner: str
    repo: str
    workflow: str = "forge-ci.yml"
    token: str | None = None

    @classmethod
    def from_env(cls) -> "GitHubActionsProvider":
        repo_full = os.environ.get("GITHUB_REPOSITORY", "salus-ryan/cortex-mvp")
        owner, repo = repo_full.split("/", 1)
        return cls(
            owner=owner,
            repo=repo,
            workflow=os.environ.get("FORGE_GITHUB_WORKFLOW", "forge-ci.yml"),
            token=os.environ.get("FORGE_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN"),
        )

    def available(self) -> bool:
        return bool(self.token and self.owner and self.repo and self.workflow)

    def dispatch(self, action: str, witness: str | None, confirmed: bool, ref: str = "master", extra: dict[str, Any] | None = None) -> dict[str, Any]:
        if action not in {"test", "build", "package"}:
            return self._refuse(f"unsupported action: {action}")
        if not witness:
            return self._refuse("github action dispatch requires witness")
        if not confirmed:
            return self._refuse("github action dispatch requires confirmed=true")
        if not self.token:
            return self._refuse("FORGE_GITHUB_TOKEN or GITHUB_TOKEN unavailable")
        inputs = {"action": action, "witness": witness, "confirmed": "true"}
        for key, value in (extra or {}).items():
            if key in {"action", "witness", "confirmed"}:
                continue
            inputs[str(key)] = str(value)
        payload = json.dumps({"ref": ref, "inputs": inputs}).encode()
        url = f"https://api.github.com/repos/{self.owner}/{self.repo}/actions/workflows/{self.workflow}/dispatches"
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "authorization": f"Bearer {self.token}",
                "accept": "application/vnd.github+json",
                "x-github-api-version": "2022-11-28",
                "content-type": "application/json",
                "user-agent": "cortex-forge",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return {
                "status": "dispatched" if resp.status in {204, 201, 200} else "unknown",
                "code": resp.status,
                "owner": self.owner,
                "repo": self.repo,
                "workflow": self.workflow,
                "ref": ref,
                "inputs": {**inputs, "token": "redacted"},
                "may_execute": False,
                "statement": "GitHub Actions workflow dispatch requested; execution occurs in GitHub runner.",
            }

    def latest_runs(self, limit: int = 5) -> dict[str, Any]:
        if not self.token:
            return self._refuse("FORGE_GITHUB_TOKEN or GITHUB_TOKEN unavailable")
        url = f"https://api.github.com/repos/{self.owner}/{self.repo}/actions/workflows/{self.workflow}/runs?per_page={max(1,min(limit,20))}"
        req = urllib.request.Request(url, headers={"authorization": f"Bearer {self.token}", "accept": "application/vnd.github+json", "user-agent": "cortex-forge"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        runs = [
            {
                "id": r.get("id"),
                "status": r.get("status"),
                "conclusion": r.get("conclusion"),
                "html_url": r.get("html_url"),
                "head_sha": r.get("head_sha"),
                "created_at": r.get("created_at"),
            }
            for r in data.get("workflow_runs", [])
        ]
        return {"status": "ok", "runs": runs, "may_execute": False}

    def _refuse(self, reason: str) -> dict[str, Any]:
        return {"status": "refused", "reason": reason, "may_execute": False}
