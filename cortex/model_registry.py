"""Deterministic model routing registry for governed Cortex inference."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ModelSpec:
    provider: str
    model: str
    tier: str
    max_context: int
    strengths: list[str]
    allowed_authorities: list[str]
    requires_network: bool = False
    requires_key_env: str = ""

    def available(self) -> bool:
        return not self.requires_key_env or bool(os.environ.get(self.requires_key_env))

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["available"] = self.available()
        return data


DEFAULT_MODELS = [
    ModelSpec("local", "local-mind-v1", "local_fast", 8000, ["offline", "law", "refusal"], ["interpret", "observe"]),
    ModelSpec("openai", "gpt-4o-mini", "remote_balanced", 32000, ["coding", "planning"], ["interpret", "observe"], True, "OPENAI_API_KEY"),
    ModelSpec("openrouter", "openai/gpt-4o-mini", "remote_router", 32000, ["coding", "planning", "fallback"], ["interpret", "observe"], True, "OPENROUTER_API_KEY"),
]


class ModelRegistry:
    """Select inference backends without granting execution authority."""

    def __init__(self, models: list[ModelSpec] | None = None) -> None:
        self.models = models or DEFAULT_MODELS

    def manifest(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "rule": "model routing proposes inference only; runtime remains authority",
            "models": [m.to_dict() for m in self.models],
        }

    def route(self, task: str, authority: str = "interpret", context: dict[str, Any] | None = None) -> dict[str, Any]:
        context = context or {}
        task_l = task.lower()
        if authority not in {"interpret", "observe"}:
            return self._local("authority outside model routing scope")

        forced_provider = context.get("provider") or os.environ.get("ORACLE_PROVIDER")
        forced_model = context.get("model") or os.environ.get("ORACLE_MODEL")
        if forced_provider in {"echo", "none"}:
            return {"provider": forced_provider, "model": forced_model or "echo", "tier": "local_echo", "reason": "explicit echo provider request", "may_execute": False, "authority": "inference_only"}
        if forced_provider:
            for spec in self.models:
                if spec.provider == forced_provider and (not forced_model or spec.model == forced_model):
                    if spec.available() and authority in spec.allowed_authorities:
                        return self._decision(spec, "explicit provider/model request")
                    return self._local(f"requested model unavailable or not allowed: {forced_provider}")

        wants_stronger = any(word in task_l for word in ["plan", "architecture", "debug", "implement", "refactor", "security", "audit", "soc 2"])
        wants_offline = bool(context.get("offline")) or any(word in task_l for word in ["offline", "private", "personal"])
        if not wants_offline and wants_stronger:
            for provider in ("openai", "openrouter"):
                for spec in self.models:
                    if spec.provider == provider and spec.available() and authority in spec.allowed_authorities:
                        return self._decision(spec, "task benefits from stronger reasoning")
        return self._local("local-first default")

    def _local(self, reason: str) -> dict[str, Any]:
        local = next(m for m in self.models if m.provider == "local")
        return self._decision(local, reason)

    @staticmethod
    def _decision(spec: ModelSpec, reason: str) -> dict[str, Any]:
        return {"provider": spec.provider, "model": spec.model, "tier": spec.tier, "reason": reason, "may_execute": False, "authority": "inference_only"}
