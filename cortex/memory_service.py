"""Governed typed memory service for Cortex."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TYPES = {"factual", "inferred", "symbolic", "project", "rejected", "personal"}


class MemoryService:
    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root).resolve()
        self.dir = self.root / "memory"
        self.dir.mkdir(parents=True, exist_ok=True)

    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _path(self, typ: str) -> Path:
        if typ not in TYPES:
            raise ValueError(f"unknown memory type: {typ}")
        return self.dir / f"{typ}.jsonl"

    def write(self, typ: str, content: str, source: str, confidence: float = 0.8, witness: str | None = None) -> dict[str, Any]:
        if not source:
            raise ValueError("memory source is required")
        if typ == "personal" and not witness:
            raise ValueError("personal memory requires witness")
        rec = {
            "id": "mem_" + uuid.uuid4().hex[:12],
            "type": typ,
            "content": content,
            "source": source,
            "confidence": confidence,
            "created_at": self.now(),
            "mutable": True,
            "witness": witness,
            "sha256": hashlib.sha256(f"{typ}:{content}:{source}".encode()).hexdigest(),
            "law": ["LAW 6", "LAW 7"],
        }
        with self._path(typ).open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")
        return rec

    def retrieve(self, query: str = "", typ: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._active_records(typ)
        q = query.lower().strip()
        if q:
            rows = [
                rec
                for rec in rows
                if q in rec.get("content", "").lower() or q in rec.get("source", "").lower() or q in rec.get("type", "").lower()
            ]
        return rows[-limit:]

    def search(self, query: str = "", typ: str | None = None, limit: int = 20) -> dict[str, Any]:
        rows = self.retrieve(query=query, typ=typ, limit=10_000)
        scored = [self.score_record(rec) for rec in rows]
        scored.sort(key=lambda rec: (rec.get("quality", {}).get("score", 0), rec.get("created_at", "")), reverse=True)
        return {"status": "ok", "query": query, "type": typ or "all", "records": scored[:limit], "may_execute": False}

    def score_record(self, rec: dict[str, Any], duplicate_count: int = 1) -> dict[str, Any]:
        """Attach an inspectable memory quality score.

        The score rewards confidence, provenance, enough content to be useful,
        freshness, and witness/law/hash metadata. Duplicate content is
        down-ranked rather than silently deleted.
        """
        reasons: list[str] = []
        confidence = max(0.0, min(1.0, float(rec.get("confidence", 0.0) or 0.0)))
        score = round(confidence * 35)
        if confidence >= 0.75:
            reasons.append("high_confidence")
        elif confidence < 0.4:
            reasons.append("low_confidence")

        if rec.get("source"):
            score += 15
            reasons.append("has_source")
        content = str(rec.get("content", ""))
        words = len(content.split())
        if words >= 8:
            score += 20
            reasons.append("useful_detail")
        elif words >= 3:
            score += 10
            reasons.append("some_detail")
        else:
            reasons.append("too_short")
        if rec.get("sha256"):
            score += 5
            reasons.append("hash_provenance")
        if rec.get("law"):
            score += 5
            reasons.append("law_tagged")
        if rec.get("witness"):
            score += 5
            reasons.append("witnessed")

        age_days = self._age_days(str(rec.get("created_at", "")))
        if age_days is None or age_days <= 30:
            score += 10
            reasons.append("recent")
        elif age_days <= 180:
            score += 5
            reasons.append("aging")
        else:
            reasons.append("stale")

        if duplicate_count > 1:
            penalty = min(25, 10 * (duplicate_count - 1))
            score -= penalty
            reasons.append(f"duplicate_penalty:{penalty}")

        score = max(0, min(100, score))
        out = dict(rec)
        out["quality"] = {
            "score": score,
            "threshold_band": "75-100" if score >= 75 else "50-74" if score >= 50 else "0-49",
            "components": {
                "confidence_points": round(confidence * 35),
                "source_points": 15 if rec.get("source") else 0,
                "detail_points": 20 if words >= 8 else 10 if words >= 3 else 0,
                "hash_points": 5 if rec.get("sha256") else 0,
                "law_points": 5 if rec.get("law") else 0,
                "witness_points": 5 if rec.get("witness") else 0,
                "freshness_points": 10 if age_days is None or age_days <= 30 else 5 if age_days <= 180 else 0,
                "duplicate_penalty": min(25, 10 * (duplicate_count - 1)) if duplicate_count > 1 else 0,
            },
            "reasons": reasons,
            "duplicate_count": duplicate_count,
            "may_execute": False,
        }
        return out

    def report(self) -> dict[str, Any]:
        records = self._active_records()
        content_counts = Counter(self._fingerprint(rec) for rec in records)
        scored = [self.score_record(rec, content_counts[self._fingerprint(rec)]) for rec in records]
        by_type: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "avg_quality": 0.0})
        for rec in scored:
            bucket = by_type[str(rec.get("type", "unknown"))]
            bucket["count"] += 1
            bucket["avg_quality"] += rec["quality"]["score"]
        for bucket in by_type.values():
            if bucket["count"]:
                bucket["avg_quality"] = round(bucket["avg_quality"] / bucket["count"], 2)
        low_quality = sorted(scored, key=lambda rec: rec["quality"]["score"])[:10]
        duplicates = [
            {"fingerprint": fp, "count": count}
            for fp, count in content_counts.most_common()
            if count > 1
        ][:10]
        avg = round(sum(rec["quality"]["score"] for rec in scored) / len(scored), 2) if scored else 0.0
        return {
            "status": "memory_report",
            "total_active": len(scored),
            "forgotten_count": len(self.forgotten_ids()),
            "avg_quality": avg,
            "by_type": dict(sorted(by_type.items())),
            "low_quality": low_quality,
            "duplicates": duplicates,
            "recommendations": self._recommendations(scored, duplicates),
            "may_execute": False,
        }

    def _active_records(self, typ: str | None = None) -> list[dict[str, Any]]:
        paths = [self._path(typ)] if typ else [self.dir / f"{t}.jsonl" for t in sorted(TYPES)]
        rows: list[dict[str, Any]] = []
        forgotten = self.forgotten_ids()
        for path in paths:
            if not path.exists():
                continue
            for line in path.read_text().splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                if rec.get("id") not in forgotten:
                    rows.append(rec)
        return rows

    def _age_days(self, created_at: str) -> int | None:
        try:
            created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            return None
        return max(0, (datetime.now(timezone.utc) - created).days)

    def _fingerprint(self, rec: dict[str, Any]) -> str:
        content = " ".join(str(rec.get("content", "")).lower().split())
        return hashlib.sha256(f"{rec.get('type')}:{content}".encode()).hexdigest()[:16]

    def _recommendations(self, scored: list[dict[str, Any]], duplicates: list[dict[str, Any]]) -> list[str]:
        recommendations: list[str] = []
        if any(rec["quality"]["score"] < 50 for rec in scored):
            recommendations.append("Review low-quality memories; enrich source/detail/confidence or forget with witness.")
        if duplicates:
            recommendations.append("Consolidate duplicate memories into one higher-quality episodic or semantic record.")
        if not scored:
            recommendations.append("No active memories; seed project, factual, and episodic memories from governed steps.")
        return recommendations

    def forgotten_ids(self) -> set[str]:
        path = self.dir / "forgotten.jsonl"
        if not path.exists():
            return set()
        ids: set[str] = set()
        for line in path.read_text().splitlines():
            if line.strip():
                ids.add(json.loads(line).get("id", ""))
        return ids

    def forget(self, memory_id: str, witness: str | None, reason: str = "user request") -> dict[str, Any]:
        if not witness:
            raise ValueError("forget requires witness")
        found = None
        for rec in self.retrieve(limit=10_000):
            if rec.get("id") == memory_id:
                found = rec
                break
        if not found:
            raise ValueError("memory id not found")
        tombstone = {
            "id": memory_id,
            "forgotten_at": self.now(),
            "witness": witness,
            "reason": reason,
            "sha256": hashlib.sha256(f"forget:{memory_id}:{reason}".encode()).hexdigest(),
            "law": ["LAW 6", "LAW 7"],
        }
        with (self.dir / "forgotten.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(tombstone, sort_keys=True) + "\n")
        return tombstone

    def export(self, typ: str | None = None) -> dict[str, Any]:
        return {"status": "ok", "type": typ or "all", "records": self.retrieve(typ=typ, limit=10_000), "forgotten_ids": sorted(self.forgotten_ids())}
