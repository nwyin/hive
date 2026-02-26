"""Tests for hive.utils: _parse_repo_name and _normalize_project_name."""

from hive.utils import _normalize_project_name, _parse_repo_name


# ── _parse_repo_name ──────────────────────────────────────────────────────────


class TestParseRepoName:
    """INV-1: _parse_repo_name always returns a bare name (no slashes)."""

    def test_ssh_org_repo_git(self):
        """SSH remote with org/repo.git → bare repo name."""
        assert _parse_repo_name("git@github.com:tnguyen21/kairos.git") == "kairos"

    def test_ssh_no_org(self):
        """SSH remote with just repo (no org) → bare repo name."""
        assert _parse_repo_name("git@github.com:myrepo.git") == "myrepo"

    def test_https_org_repo_git(self):
        """HTTPS remote with org/repo.git → bare repo name."""
        assert _parse_repo_name("https://github.com/tnguyen21/kairos.git") == "kairos"

    def test_https_no_git_suffix(self):
        """HTTPS remote without .git suffix → bare repo name."""
        assert _parse_repo_name("https://github.com/org/hive") == "hive"

    def test_ssh_no_git_suffix(self):
        """SSH remote without .git suffix → bare repo name."""
        assert _parse_repo_name("git@github.com:org/hive") == "hive"

    def test_result_has_no_slash(self):
        """Returned value never contains a slash — INV-1 invariant."""
        urls = [
            "git@github.com:org/repo.git",
            "https://github.com/org/repo.git",
            "git@gitlab.com:deep/nested/repo.git",
            "https://gitlab.com/a/b/c/repo",
        ]
        for url in urls:
            result = _parse_repo_name(url)
            assert result is not None
            assert "/" not in result, f"Got slash in result for {url!r}: {result!r}"

    def test_trailing_slash_stripped(self):
        """Trailing slashes in the URL are ignored."""
        assert _parse_repo_name("https://github.com/org/repo/") == "repo"

    def test_empty_string_returns_none(self):
        """Empty or whitespace-only input returns None."""
        assert _parse_repo_name("") is None
        assert _parse_repo_name("   ") is None

    def test_hive_project(self):
        """The hive repo itself parses correctly."""
        assert _parse_repo_name("git@github.com:tau/hive.git") == "hive"
        assert _parse_repo_name("https://github.com/tau/hive.git") == "hive"


# ── _normalize_project_name ───────────────────────────────────────────────────


class TestNormalizeProjectName:
    """INV-2: _normalize_project_name always returns a bare repo name."""

    def test_plain_name_unchanged(self):
        """A name without slashes is returned as-is."""
        assert _normalize_project_name("kairos") == "kairos"
        assert _normalize_project_name("hive") == "hive"
        assert _normalize_project_name("my-project") == "my-project"

    def test_org_slash_repo(self):
        """'org/repo' form is normalized to 'repo'."""
        assert _normalize_project_name("tnguyen21/kairos") == "kairos"
        assert _normalize_project_name("org/hive") == "hive"

    def test_multiple_slashes(self):
        """Deep paths like 'a/b/c' return the last segment."""
        assert _normalize_project_name("a/b/c") == "c"

    def test_consistency_with_detect_project(self):
        """Normalized output matches what _parse_repo_name returns for the same repo."""
        # Both should agree on the bare repo name
        ssh_url = "git@github.com:org/myrepo.git"
        from_url = _parse_repo_name(ssh_url)
        from_name = _normalize_project_name("org/myrepo")
        assert from_url == from_name == "myrepo"
