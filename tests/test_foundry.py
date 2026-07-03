from cortex.foundry import FoundryRegistry


def test_foundry_registry_prioritizes_tool_algebra():
    data = FoundryRegistry().repos()
    assert data["status"] == "ok"
    assert data["may_execute"] is False
    assert data["repos"][0]["name"] == "tool-algebra-plugin"
    assert "PII" in data["repos"][0]["import_goal"]


def test_foundry_plan_is_research_only():
    plan = FoundryRegistry().plan()
    assert plan["next_import"]["name"] == "tool-algebra-plugin"
    assert "do not clone or execute" in plan["rule"]
    assert plan["may_execute"] is False
