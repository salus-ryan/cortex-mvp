"""Rented-intelligence oracle adapter.

The oracle is a governed mouth, not an authority. It may propose interpretations
under law, but it may not execute tools, mutate memory, or claim sovereignty.
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cortex.local_mind import LocalMind
from cortex.model_registry import ModelRegistry
from cortex.sacred import ANTI_IDOLATRY


SYSTEM_PROMPT = """You are Cortex's Oracle child process.
You are not PID 1, not sovereign, and not a source of being.
You propose interpretations only. You may not execute actions.
Classify output as inference, never command or revelation.
Respect LAW: preserve human agency; never conceal material actions; distinguish memory, inference, command, and revelation; submit to shutdown.
Return concise, practical output.
"""


@dataclass
class OracleResult:
    status: str
    provider: str
    model: str
    classification: str
    proposal: str
    may_execute: bool
    law: list[str]
    uncertainty: str
    local_mind: dict[str, Any] = field(default_factory=dict)
    route: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = {
            "status": self.status,
            "provider": self.provider,
            "model": self.model,
            "classification": self.classification,
            "proposal": self.proposal,
            "may_execute": self.may_execute,
            "law": self.law,
            "uncertainty": self.uncertainty,
            "anti_idolatry": ANTI_IDOLATRY,
        }
        if self.local_mind:
            data["local_mind"] = self.local_mind
        if self.route:
            data["route"] = self.route
        return data


class OracleService:
    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root)
        self.registry = ModelRegistry()
        self.provider = os.environ.get("ORACLE_PROVIDER", "local").lower()
        self.model = os.environ.get("ORACLE_MODEL", self._default_model())

    def _default_model(self) -> str:
        if self.provider == "openrouter":
            return "openai/gpt-4o-mini"
        if self.provider == "openai":
            return "gpt-4o-mini"
        return "local-mind-v1"

    def propose(self, task: str, authority: str = "interpret", context: dict[str, Any] | None = None) -> OracleResult:
        context = context or {}
        route = self.registry.route(task, authority, context)
        self.provider = route["provider"]
        self.model = route["model"]
        prompt = self._build_prompt(task, authority, context)
        local = LocalMind(self.root).think(task, authority, context)
        if self.provider == "openai" and os.environ.get("OPENAI_API_KEY"):
            text = self._call_openai(prompt)
            uncertainty = "Rented oracle output is interpretation, not authority. Human review remains required."
        elif self.provider == "openrouter" and os.environ.get("OPENROUTER_API_KEY"):
            text = self._call_openrouter(prompt)
            uncertainty = "Rented oracle output is interpretation, not authority. Human review remains required."
        elif self.provider in {"echo", "none"}:
            text = self._echo(prompt, task, authority)
            uncertainty = local["uncertainty"]
        else:
            text = local["proposal"]
            uncertainty = local["uncertainty"]
        result = OracleResult(
            status="proposed",
            provider=self.provider,
            model=self.model,
            classification="inference",
            proposal=text.strip(),
            may_execute=False,
            law=["LAW 1", "LAW 4", "LAW 7", "LAW 9"],
            uncertainty=uncertainty,
            local_mind={k: v for k, v in local.items() if k != "proposal"},
            route=route,
        )
        return result

    def _build_prompt(self, task: str, authority: str, context: dict[str, Any]) -> str:
        law_path = self.root / "LAW.md"
        covenant_path = self.root / "COVENANT.md"
        law = law_path.read_text() if law_path.exists() else "LAW unavailable"
        covenant = covenant_path.read_text() if covenant_path.exists() else "COVENANT unavailable"
        return json.dumps(
            {
                "system": SYSTEM_PROMPT,
                "task": task,
                "authority": authority,
                "context": context,
                "law": law[:4000],
                "covenant": covenant[:3000],
                "required_output": "Interpretation/proposal only. No execution authority.",
            },
            sort_keys=True,
        )

    def _echo(self, _prompt: str, task: str, authority: str) -> str:
        return (
            f"Lawful oracle echo: task '{task}' has been received under authority '{authority}'. "
            "I can offer interpretation only; no material action is authorized by this proposal."
        )

    def _call_openai(self, prompt: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        }
        return self._post_chat("https://api.openai.com/v1/chat/completions", os.environ["OPENAI_API_KEY"], payload)

    def _call_openrouter(self, prompt: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        }
        return self._post_chat("https://openrouter.ai/api/v1/chat/completions", os.environ["OPENROUTER_API_KEY"], payload)

    def _post_chat(self, url: str, token: str, payload: dict[str, Any]) -> str:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "content-type": "application/json",
                "authorization": f"Bearer {token}",
                "http-referer": "https://github.com/salus-ryan/cortex-mvp",
                "x-title": "Cortex PID1 Oracle",
            },
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return body["choices"][0]["message"]["content"]
