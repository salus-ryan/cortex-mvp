from cortex.model_registry import ModelRegistry
from cortex.oracle import OracleService


def test_model_registry_defaults_local_without_keys(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ORACLE_PROVIDER", raising=False)
    route = ModelRegistry().route("implement an audit plan", "interpret")
    assert route["provider"] == "local"
    assert route["may_execute"] is False


def test_model_registry_routes_to_available_remote_for_harder_task(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("ORACLE_PROVIDER", raising=False)
    route = ModelRegistry().route("implement a security architecture", "interpret")
    assert route["provider"] == "openai"
    assert route["authority"] == "inference_only"


def test_model_registry_refuses_non_inference_authority(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    route = ModelRegistry().route("deploy now", "execute")
    assert route["provider"] == "local"
    assert "authority" in route["reason"]


def test_oracle_result_includes_route(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ORACLE_PROVIDER", raising=False)
    result = OracleService(tmp_path).propose("hello", "interpret", {})
    data = result.to_dict()
    assert data["route"]["provider"] == "local"
    assert data["route"]["may_execute"] is False
