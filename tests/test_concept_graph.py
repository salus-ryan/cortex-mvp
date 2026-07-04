from pathlib import Path

from cortex.concept_graph import ConceptGraphService
from cortex.memory_service import MemoryService


def test_concept_graph_uses_witnessed_personal_memory(tmp_path: Path):
    mem = MemoryService(tmp_path)
    mem.write("personal", "Ryan cares about local-first cortex audit tools", "test", 0.9, witness="human")
    mem.write("personal", "Ryan prefers mobile cortex workflows and audit visibility", "test", 0.9, witness="human")

    graph = ConceptGraphService(tmp_path).graph()

    assert graph["status"] == "ok"
    labels = {n["label"] for n in graph["nodes"]}
    assert "audit" in labels
    assert "mobile" in labels
    assert graph["may_execute"] is False
    assert graph["edges"]


def test_concept_graph_empty_is_safe(tmp_path: Path):
    graph = ConceptGraphService(tmp_path).graph()
    assert graph["nodes"] == []
    assert graph["edges"] == []
    assert "witnessed" in graph["rule"]
