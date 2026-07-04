"""
tool_registry.py — Cortex Tool Registry

Defines the allowlisted tool surface, risk tiers, and unit costs.
The runtime harness consults this registry before executing any tool call.

MVP allowlist:
  shell.readonly   — inspect files, read state (read_only)
  shell.patch      — apply minimal patch inside workspace (write_limited)
  pytest           — run test suite (verify)
  git.diff         — inspect diff (read_only)
  memory.read      — read governed memory (memory)
  memory.write     — write governed memory (memory)
  scl.parse        — parse and validate SCL string (read_only)
  scl.emit         — emit canonical SCL string (read_only)
  budget.check     — inspect current budget (read_only)

Risk tiers:
  read_only      — inspect files, inspect state, query memory
  write_limited  — apply patch only inside workspace
  verify         — run tests, schema checks, policy checks
  memory         — read/write governed memory
  deny           — explicitly reject unsafe action
  halt           — stop with success, blocked, or failure state
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional


class RiskTier(str):
    READ_ONLY = "read_only"
    WRITE_LIMITED = "write_limited"
    VERIFY = "verify"
    MEMORY = "memory"
    DENY = "deny"
    HALT = "halt"


@dataclass
class ToolSpec:
    """Specification for a registered tool."""

    name: str
    description: str
    risk_tier: str
    unit_cost: int
    required_capability: str
    postconditions: tuple[str, ...]
    sandbox: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    handler: Optional[Callable[..., Any]] = field(default=None, repr=False)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "risk_tier": self.risk_tier,
            "unit_cost": self.unit_cost,
            "required_capability": self.required_capability,
            "postconditions": list(self.postconditions),
            "sandbox": self.sandbox,
            "enabled": self.enabled,
        }


@dataclass
class ExecutionResult:
    """Result of executing a tool call."""

    tool: str
    success: bool
    output: str = ""
    error: str = ""
    cost: int = 0

    @property
    def summary(self) -> str:
        if self.success:
            return self.output[:500] if self.output else "ok"
        return f"error: {self.error}"


class ToolRegistry:
    """
    Registry of all permitted tools.

    Only tools registered here may be invoked by the runtime.
    All others are denied at the policy layer.
    """

    def __init__(self, workspace: str = "/workspace") -> None:
        self.workspace = workspace
        self._tools: dict[str, ToolSpec] = {}
        self._register_defaults()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def _register_defaults(self) -> None:
        """Register the MVP tool allowlist."""
        defaults = [
            ToolSpec(
                name="shell.readonly",
                description="Read files and inspect state using safe read-only shell commands.",
                risk_tier=RiskTier.READ_ONLY,
                unit_cost=1,
                required_capability="workspace.read",
                postconditions=("no_workspace_write", "output_size_limited", "shell_false", "timeout_enforced"),
                sandbox={"shell": False, "cwd": "workspace", "timeout_seconds": 10, "write_access": False},
                handler=self._handle_shell_readonly,
            ),
            ToolSpec(
                name="shell.patch",
                description="Apply a minimal patch to a file inside the permitted workspace.",
                risk_tier=RiskTier.WRITE_LIMITED,
                unit_cost=5,
                required_capability="workspace.patch",
                postconditions=("target_confined_to_workspace", "target_exists", "patch_command_exit_zero", "timeout_enforced"),
                sandbox={"shell": False, "cwd": "workspace", "timeout_seconds": 10, "write_access": "target_file_only"},
                handler=self._handle_shell_patch,
            ),
            ToolSpec(
                name="pytest",
                description="Run pytest on a specified test file or directory.",
                risk_tier=RiskTier.VERIFY,
                unit_cost=3,
                required_capability="verify.tests",
                postconditions=("exit_code_zero", "output_size_limited", "timeout_enforced"),
                sandbox={"shell": False, "cwd": "workspace", "timeout_seconds": 60, "write_access": "test_artifacts_only"},
                handler=self._handle_pytest,
            ),
            ToolSpec(
                name="git.diff",
                description="Inspect git diff output.",
                risk_tier=RiskTier.READ_ONLY,
                unit_cost=1,
                required_capability="repo.diff",
                postconditions=("exit_code_zero", "output_size_limited", "timeout_enforced"),
                sandbox={"shell": False, "cwd": "workspace", "timeout_seconds": 10, "write_access": False},
                handler=self._handle_git_diff,
            ),
            ToolSpec(
                name="memory.read",
                description="Read from governed memory.",
                risk_tier=RiskTier.MEMORY,
                unit_cost=1,
                required_capability="memory.read",
                postconditions=("forgotten_records_excluded", "limit_enforced"),
                sandbox={"shell": False, "write_access": False},
                handler=None,  # handled by runtime directly
            ),
            ToolSpec(
                name="memory.write",
                description="Write to governed memory.",
                risk_tier=RiskTier.MEMORY,
                unit_cost=1,
                required_capability="memory.write",
                postconditions=("type_validated", "source_required", "sha256_recorded"),
                sandbox={"shell": False, "write_access": "memory_jsonl_only"},
                handler=None,
            ),
            ToolSpec(
                name="scl.parse",
                description="Parse and validate an SCL control record string.",
                risk_tier=RiskTier.READ_ONLY,
                unit_cost=1,
                required_capability="scl.parse",
                postconditions=("syntax_validated", "error_reported"),
                sandbox={"shell": False, "write_access": False},
                handler=self._handle_scl_parse,
            ),
            ToolSpec(
                name="scl.emit",
                description="Emit a canonical SCL string from components.",
                risk_tier=RiskTier.READ_ONLY,
                unit_cost=1,
                required_capability="scl.emit",
                postconditions=("canonical_string_returned", "error_reported"),
                sandbox={"shell": False, "write_access": False},
                handler=self._handle_scl_emit,
            ),
            ToolSpec(
                name="budget.check",
                description="Inspect current budget state.",
                risk_tier=RiskTier.READ_ONLY,
                unit_cost=0,
                required_capability="budget.read",
                postconditions=("remaining_units_reported",),
                sandbox={"shell": False, "write_access": False},
                handler=None,
            ),
        ]
        for spec in defaults:
            self.register(spec)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def exists(self, name: str) -> bool:
        return name in self._tools

    def is_enabled(self, name: str) -> bool:
        return self._tools.get(name, self._missing_spec()).enabled

    def cost(self, name: str) -> int:
        return self._tools.get(name, self._missing_spec()).unit_cost

    def risk_tier(self, name: str) -> str:
        return self._tools.get(name, self._missing_spec()).risk_tier

    def capability(self, name: str) -> str:
        return self._tools.get(name, self._missing_spec()).required_capability

    def postconditions(self, name: str) -> tuple[str, ...]:
        return self._tools.get(name, self._missing_spec()).postconditions

    def sandbox_profile(self, name: str) -> dict[str, Any]:
        return dict(self._tools.get(name, self._missing_spec()).sandbox)

    def postcondition_coverage_report(self) -> dict[str, Any]:
        manifest = [spec.to_dict() for spec in self._tools.values() if spec.enabled]
        missing = [spec["name"] for spec in manifest if not spec["postconditions"]]
        sandboxed = [spec["name"] for spec in manifest if spec["sandbox"]]
        return {
            "status": "tool_postcondition_coverage",
            "tool_count": len(manifest),
            "tools_with_postconditions": len(manifest) - len(missing),
            "coverage_ratio": 1.0 if not manifest else round((len(manifest) - len(missing)) / len(manifest), 3),
            "missing_postconditions": missing,
            "sandboxed_tools": sandboxed,
            "may_execute": False,
        }

    def manifest(self) -> list[dict]:
        """Return the tool manifest for prompt injection."""
        return [spec.to_dict() for spec in self._tools.values() if spec.enabled]

    def manifest_names(self) -> list[str]:
        return [spec.name for spec in self._tools.values() if spec.enabled]

    def _missing_spec(self) -> ToolSpec:
        return ToolSpec("", "", "unknown", 0, "", (), {}, False)

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute(self, name: str, args: str = "", **kwargs: Any) -> ExecutionResult:
        """
        Execute a registered tool.

        Args:
            name: Tool name.
            args: Primary argument string.
            **kwargs: Additional keyword arguments.

        Returns:
            ExecutionResult with output or error.
        """
        spec = self._tools.get(name)
        if not spec:
            return ExecutionResult(tool=name, success=False, error=f"tool '{name}' not found")
        if not spec.enabled:
            return ExecutionResult(tool=name, success=False, error=f"tool '{name}' is disabled")
        if not spec.handler:
            return ExecutionResult(tool=name, success=True, output="(handled by runtime)", cost=spec.unit_cost)

        try:
            result = spec.handler(args=args, **kwargs)
            result.cost = spec.unit_cost
            return result
        except Exception as exc:
            return ExecutionResult(tool=name, success=False, error=str(exc), cost=spec.unit_cost)

    # ------------------------------------------------------------------
    # Tool handlers
    # ------------------------------------------------------------------

    def _handle_shell_readonly(self, args: str = "", **_: Any) -> ExecutionResult:
        """Execute a safe read-only shell command."""
        # Allowlist of safe read-only commands
        safe_prefixes = (
            "cat ", "head ", "tail ", "sed ", "grep ", "ls ", "find ",
            "wc ", "diff ", "echo ", "pwd", "date",
        )
        cmd = args.strip()
        argv = shlex.split(cmd)
        if not argv:
            return ExecutionResult(tool="shell.readonly", success=False, error="empty command")
        if any(chr(code) in cmd for code in (59, 124, 96, 62, 60, 10)):
            return ExecutionResult(tool="shell.readonly", success=False, error="control syntax is not allowed")
        if not any(argv[0] == p.strip() for p in safe_prefixes):
            return ExecutionResult(
                tool="shell.readonly",
                success=False,
                error=f"command '{argv[0]}' is not in the read-only allowlist",
            )
        if "/etc/" in cmd or "/dev/" in cmd:
            return ExecutionResult(tool="shell.readonly", success=False, error="absolute system paths are not allowed")
        try:
            proc = subprocess.run(
                argv,
                shell=False,
                capture_output=True,
                text=True,
                timeout=10,
                cwd=self.workspace if Path(self.workspace).exists() else None,
            )
            output = proc.stdout + proc.stderr
            return ExecutionResult(tool="shell.readonly", success=proc.returncode == 0, output=output)
        except subprocess.TimeoutExpired:
            return ExecutionResult(tool="shell.readonly", success=False, error="command timed out")

    def _handle_shell_patch(self, args: str = "", target: str = "", strategy: str = "minimal_change", **_: Any) -> ExecutionResult:
        """Apply a patch to a file inside the workspace."""
        # In MVP, args contains the patch content as a unified diff string
        # or a simple line replacement specification
        if not target:
            return ExecutionResult(tool="shell.patch", success=False, error="'target' field required for shell.patch")

        target_path = Path(self.workspace) / target if not Path(target).is_absolute() else Path(target)

        # Confine to workspace
        try:
            target_path.resolve().relative_to(Path(self.workspace).resolve())
        except ValueError:
            return ExecutionResult(
                tool="shell.patch",
                success=False,
                error=f"target '{target}' is outside permitted workspace",
            )

        if not target_path.exists():
            return ExecutionResult(tool="shell.patch", success=False, error=f"target file '{target}' does not exist")

        # Write patch content to a temp file and apply
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".patch", delete=False) as f:
            f.write(args)
            patch_file = f.name

        try:
            proc = subprocess.run(
                ["patch", "-p1", str(target_path), patch_file],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return ExecutionResult(
                tool="shell.patch",
                success=proc.returncode == 0,
                output=proc.stdout,
                error=proc.stderr if proc.returncode != 0 else "",
            )
        except Exception as exc:
            return ExecutionResult(tool="shell.patch", success=False, error=str(exc))
        finally:
            Path(patch_file).unlink(missing_ok=True)

    def _handle_pytest(self, args: str = "", **_: Any) -> ExecutionResult:
        """Run pytest on the specified target."""
        cmd = ["python3", "-m", "pytest", "--tb=short", "-q"]
        if args.strip():
            cmd.extend(args.strip().split())
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                cwd=self.workspace if Path(self.workspace).exists() else None,
            )
            output = proc.stdout + proc.stderr
            return ExecutionResult(
                tool="pytest",
                success=proc.returncode == 0,
                output=output[:2000],
                error="" if proc.returncode == 0 else f"pytest exited with code {proc.returncode}",
            )
        except subprocess.TimeoutExpired:
            return ExecutionResult(tool="pytest", success=False, error="pytest timed out")

    def _handle_git_diff(self, args: str = "", **_: Any) -> ExecutionResult:
        """Run git diff."""
        cmd = ["git", "diff"] + (args.strip().split() if args.strip() else [])
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10,
                cwd=self.workspace if Path(self.workspace).exists() else None,
            )
            return ExecutionResult(
                tool="git.diff",
                success=proc.returncode == 0,
                output=proc.stdout[:2000],
                error=proc.stderr if proc.returncode != 0 else "",
            )
        except Exception as exc:
            return ExecutionResult(tool="git.diff", success=False, error=str(exc))

    def _handle_scl_parse(self, args: str = "", **_: Any) -> ExecutionResult:
        """Parse and validate an SCL string."""
        from cortex.scl_parser import parse
        result = parse(args)
        if result.valid:
            return ExecutionResult(tool="scl.parse", success=True, output=result.action.to_json())
        return ExecutionResult(tool="scl.parse", success=False, error=result.error)

    def _handle_scl_emit(self, args: str = "", **_: Any) -> ExecutionResult:
        """Emit a canonical SCL string (args is JSON of components)."""
        import json as _json
        try:
            parts = _json.loads(args)
            from cortex.scl_parser import emit
            scl_str = emit(parts["anchor"], parts["relation"], **parts.get("fields", {}))
            return ExecutionResult(tool="scl.emit", success=True, output=scl_str)
        except Exception as exc:
            return ExecutionResult(tool="scl.emit", success=False, error=str(exc))
