from pathlib import Path

from cortex.file_mentions import enrich_text_with_file_mentions, resolve_file_mentions


def test_enrich_file_mentions_includes_workspace_file(tmp_path: Path):
    (tmp_path / "notes.md").write_text("agreed context", encoding="utf-8")

    enriched, meta = enrich_text_with_file_mentions(tmp_path, "please read @{notes.md}")

    assert meta["mentions"][0]["status"] == "included"
    assert meta["mentions"][0]["path"] == "notes.md"
    assert "agreed context" in enriched


def test_file_mentions_refuse_workspace_escape(tmp_path: Path):
    meta = resolve_file_mentions(tmp_path, "nope @{../outside.txt}")

    assert meta["mentions"][0]["status"] == "refused"
    assert "escapes" in meta["mentions"][0]["reason"]
