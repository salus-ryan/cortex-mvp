"""Inspectable concept graph for witnessed Cortex memories."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cortex.memory_service import MemoryService

STOPWORDS = {
    "about", "after", "also", "because", "before", "being", "cortex", "could", "from", "have", "into", "like", "more", "need", "only", "should", "that", "their", "there", "they", "this", "through", "want", "what", "when", "where", "with", "would", "your",
}


@dataclass(frozen=True)
class ConceptNode:
    id: str
    label: str
    weight: int
    sources: list[str]


@dataclass(frozen=True)
class ConceptEdge:
    source: str
    target: str
    weight: int


class ConceptGraphService:
    """Build a small explainable graph from witnessed personal memories."""

    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root).resolve()
        self.memory = MemoryService(self.root)

    def graph(self, limit: int = 50) -> dict[str, Any]:
        records = self.memory.retrieve(typ="personal", limit=limit)
        node_counts: Counter[str] = Counter()
        node_sources: dict[str, set[str]] = defaultdict(set)
        edge_counts: Counter[tuple[str, str]] = Counter()

        for rec in records:
            content = str(rec.get("content", ""))
            concepts = self._concepts(content)
            rec_id = str(rec.get("id", "unknown"))
            for concept in concepts:
                node_counts[concept] += 1
                node_sources[concept].add(rec_id)
            for i, a in enumerate(concepts):
                for b in concepts[i + 1 :]:
                    edge_counts[tuple(sorted((a, b)))] += 1

        nodes = [
            {"id": f"concept:{label}", "label": label, "weight": weight, "sources": sorted(node_sources[label])}
            for label, weight in node_counts.most_common(40)
        ]
        node_ids = {n["label"] for n in nodes}
        edges = [
            {"source": f"concept:{a}", "target": f"concept:{b}", "weight": weight}
            for (a, b), weight in edge_counts.most_common(80)
            if a in node_ids and b in node_ids
        ]
        return {
            "status": "ok",
            "nodes": nodes,
            "edges": edges,
            "records": len(records),
            "may_execute": False,
            "rule": "graph is derived from explicit witnessed memories and remains forgettable via memory IDs",
        }

    @staticmethod
    def _concepts(text: str) -> list[str]:
        words = [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", text)]
        concepts: list[str] = []
        seen: set[str] = set()
        for word in words:
            if word in STOPWORDS or word in seen:
                continue
            seen.add(word)
            concepts.append(word)
        return concepts[:12]
