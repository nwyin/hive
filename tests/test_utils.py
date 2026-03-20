"""Tests for hive.utils: _git_remote_name and _normalize_project_name."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from hive.utils import _git_remote_name, _normalize_project_name


# ── _git_remote_name (URL parsing inlined) ────────────────────────────────────


def _make_git_result(url: str, returncode: int = 0):
    """Build a mock subprocess.CompletedProcess for git remote get-url."""
    mock = MagicMock()
    mock.returncode = returncode
    mock.stdout = url
    return mock


class TestGitRemoteName:
    """INV-1: _git_remote_name always returns a bare repo name (no slashes)."""

    def _call(self, url: str) -> str | None:
        with patch("hive.utils.subprocess.run", return_value=_make_git_result(url)):
            return _git_remote_name(Path("/fake/root"))

    def test_ssh_org_repo_git(self):
        """SSH remote with org/repo.git → bare repo name."""
        assert self._call("git@github.com:nwyin/kairos.git") == "kairos"

    def test_ssh_no_org(self):
        """SSH remote with just repo (no org) → bare repo name."""
        assert self._call("git@github.com:myrepo.git") == "myrepo"

    def test_https_org_repo_git(self):
        """HTTPS remote with org/repo.git → bare repo name."""
        assert self._call("https://github.com/nwyin/kairos.git") == "kairos"

    def test_https_no_git_suffix(self):
        """HTTPS remote without .git suffix → bare repo name."""
        assert self._call("https://github.com/org/hive") == "hive"

    def test_ssh_no_git_suffix(self):
        """SSH remote without .git suffix → bare repo name."""
        assert self._call("git@github.com:org/hive") == "hive"

    def test_result_has_no_slash(self):
        """Returned value never contains a slash — INV-1 invariant."""
        urls = [
            "git@github.com:org/repo.git",
            "https://github.com/org/repo.git",
            "git@gitlab.com:deep/nested/repo.git",
            "https://gitlab.com/a/b/c/repo",
        ]
        for url in urls:
            result = self._call(url)
            assert result is not None
            assert "/" not in result, f"Got slash in result for {url!r}: {result!r}"

    def test_trailing_slash_stripped(self):
        """Trailing slashes in the URL are ignored."""
        assert self._call("https://github.com/org/repo/") == "repo"

    def test_empty_stdout_returns_none(self):
        """Empty stdout (no remote configured) returns None."""
        with patch("hive.utils.subprocess.run", return_value=_make_git_result("", returncode=128)):
            assert _git_remote_name(Path("/fake/root")) is None

    def test_hive_project(self):
        """The hive repo itself parses correctly."""
        assert self._call("git@github.com:tau/hive.git") == "hive"
        assert self._call("https://github.com/tau/hive.git") == "hive"

    def test_git_not_found_returns_none(self):
        """FileNotFoundError (git not on PATH) returns None."""
        with patch("hive.utils.subprocess.run", side_effect=FileNotFoundError):
            assert _git_remote_name(Path("/fake/root")) is None

    def test_timeout_returns_none(self):
        """TimeoutExpired returns None."""
        import subprocess

        with patch("hive.utils.subprocess.run", side_effect=subprocess.TimeoutExpired("git", 5)):
            assert _git_remote_name(Path("/fake/root")) is None


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
        assert _normalize_project_name("nwyin/kairos") == "kairos"
        assert _normalize_project_name("org/hive") == "hive"

    def test_multiple_slashes(self):
        """Deep paths like 'a/b/c' return the last segment."""
        assert _normalize_project_name("a/b/c") == "c"

    def test_consistency_with_git_remote_name(self):
        """_normalize_project_name agrees with _git_remote_name for the same repo."""
        ssh_url = "git@github.com:org/myrepo.git"
        with patch("hive.utils.subprocess.run", return_value=_make_git_result(ssh_url)):
            from_url = _git_remote_name(Path("/fake/root"))
        from_name = _normalize_project_name("org/myrepo")
        assert from_url == from_name == "myrepo"
