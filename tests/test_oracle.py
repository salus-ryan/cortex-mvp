from pathlib import Path

from cortex.oracle import OracleService


def test_oracle_echo_is_inference_and_not_executable(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("ORACLE_PROVIDER", "echo")
    result = OracleService(tmp_path).propose("interpret law", "interpret")
    data = result.to_dict()
    assert data["status"] == "proposed"
    assert data["classification"] == "inference"
    assert data["may_execute"] is False
    assert "Lawful oracle echo" in data["proposal"]
