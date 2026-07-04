from pathlib import Path

from cortex.tool_registry import ToolRegistry


def test_readonly_rejects_command_name_prefix_confusion(tmp_path: Path):
    result = ToolRegistry(str(tmp_path)).execute("shell.readonly", args="pwdx")
    assert not result.success
    assert "allowlist" in result.error


def test_readonly_rejects_semicolon_control_syntax(tmp_path: Path):
    marker = tmp_path / "pwned"
    result = ToolRegistry(str(tmp_path)).execute("shell.readonly", args=f"echo ok; touch {marker}")
    assert not result.success
    assert not marker.exists()


def test_readonly_uses_shlex_for_quoted_args(tmp_path: Path):
    result = ToolRegistry(str(tmp_path)).execute("shell.readonly", args='echo "hello world"')
    assert result.success
    assert "hello world" in result.output


def test_readonly_blocks_known_sensitive_system_paths(tmp_path: Path):
    result = ToolRegistry(str(tmp_path)).execute("shell.readonly", args="cat /etc/passwd")
    assert not result.success
