from pathlib import Path

from cortex.tool_algebra import ToolAlgebra
from cortex.tool_gateway import ToolGateway


def test_tool_algebra_detects_and_redacts_pii():
    alg = ToolAlgebra()
    text = "email ryan@example.com ssn 123-45-6789 token Bearer abcdefghijklmnopqrstuvwxyz"
    result = alg.validate_output("read_file", text)
    kinds = {t["kind"] for t in result["pii_taints"]}
    assert {"email", "ssn", "bearer"}.issubset(kinds)
    assert "ryan@example.com" not in result["safe_output"]
    assert result["redacted"] is True
    assert result["may_execute"] is False


def test_tool_algebra_verify_claim():
    alg = ToolAlgebra()
    good = alg.verify_claim("Cortex runs as PID 1", ["The /pid1 endpoint says pid 1 and is_pid1 true for Cortex."])
    bad = alg.verify_claim("Cortex owns a moon base", ["Cortex has a mobile app."])
    assert good["status"] == "supported"
    assert bad["status"] == "unsupported"


def test_tool_gateway_redacts_read_file_output(tmp_path: Path):
    (tmp_path / "runtime").mkdir()
    (tmp_path / "ledger").mkdir()
    (tmp_path / "runtime" / "permissions.json").write_text('{"authority_levels":{"observe":{"tools":[],"requires_confirmation":false}}}')
    (tmp_path / "secret.txt").write_text("contact ryan@example.com")
    result = ToolGateway(tmp_path).execute("read_file", {"path": "secret.txt"}, "observe")
    assert result["status"] == "completed"
    assert "ryan@example.com" not in result["output"]
    assert result["validation"]["redacted"] is True
