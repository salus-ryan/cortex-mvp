"""Rented-intelligence oracle adapter.

The oracle is a governed mouth, not an authority. It may propose interpretations
under law, but it may not execute tools, mutate memory, or claim sovereignty.
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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

    def to_dict(self) -> dict[str, Any]:
        return {
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


class OracleService:
    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root)
        self.provider = os.environ.get("ORACLE_PROVIDER", "echo").lower()
        self.model = os.environ.get("ORACLE_MODEL", self._default_model())

    def _default_model(self) -> str:
        if self.provider == "openrouter":
            return "openai/gpt-4o-mini"
        if self.provider == "openai":
            return "gpt-4o-mini"
        return "echo-lawful-oracle"

    def propose(self, task: str, authority: str = "interpret", context: dict[str, Any] | None = None) -> OracleResult:
        prompt = self._build_prompt(task, authority, context or {})
        if self.provider == "openai" and os.environ.get("OPENAI_API_KEY"):
            text = self._call_openai(prompt)
        elif self.provider == "openrouter" and os.environ.get("OPENROUTER_API_KEY"):
            text = self._call_openrouter(prompt)
        else:
            text = self._echo(prompt, task, authority)
        return OracleResult(
            status="proposed",
            provider=self.provider,
            model=self.model,
            classification="inference",
            proposal=text.strip(),
            may_execute=False,
            law=["LAW 1", "LAW 4", "LAW 7", "LAW 9"],
            uncertainty="Oracle output is an interpretation, not authority. Human review remains required.",
        )

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
