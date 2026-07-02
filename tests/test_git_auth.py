from cortex.git_auth import GitAuthStatus


def test_git_auth_status_serializes_without_secret_values():
    status = GitAuthStatus(
        remote="https://github.com/example/repo.git",
        can_fetch=True,
        can_push_dry_run=False,
        auth_sources=["environment:GITHUB_TOKEN"],
        safe_next_steps=["configure token"],
        errors=["auth failed"],
    )
    data = status.to_dict()
    assert data["auth_sources"] == ["environment:GITHUB_TOKEN"]
    assert "token-value" not in str(data).lower()
