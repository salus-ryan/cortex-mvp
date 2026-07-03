from cortex.pid1 import child_specs_for_profile


def names(specs):
    return [spec.name for spec in specs]


def test_compact_profile_has_core_only():
    assert names(child_specs_for_profile("compact")) == [
        "web",
        "guardian",
        "scribe",
        "oracle",
        "memory",
        "immune",
    ]


def test_tiny_profile_is_web_only():
    assert names(child_specs_for_profile("tiny")) == ["web"]


def test_child_allowlist_overrides_profile():
    assert names(child_specs_for_profile("full", "web,oracle")) == ["web", "oracle"]
