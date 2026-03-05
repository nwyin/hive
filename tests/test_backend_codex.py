from pathlib import Path

from hive.backends.backend_codex import CodexAppServerBackend


def test_compute_git_sandbox_writable_roots_none():
    assert CodexAppServerBackend._compute_git_sandbox_writable_roots(None) == []


def test_compute_git_sandbox_writable_roots_non_worktree(tmp_path: Path):
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").mkdir()
    assert CodexAppServerBackend._compute_git_sandbox_writable_roots(str(wt)) == []


def test_compute_git_sandbox_writable_roots_invalid_marker(tmp_path: Path):
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text("not a worktree\n")
    assert CodexAppServerBackend._compute_git_sandbox_writable_roots(str(wt)) == []


def test_compute_git_sandbox_writable_roots_worktree_absolute(tmp_path: Path):
    repo = tmp_path / "repo"
    wt = repo / ".worktrees" / "agent1"
    gitdir = repo / ".git" / "worktrees" / "agent1"
    gitdir.mkdir(parents=True)
    wt.mkdir(parents=True)
    (wt / ".git").write_text(f"gitdir: {gitdir}\n")

    roots = CodexAppServerBackend._compute_git_sandbox_writable_roots(str(wt))
    assert roots == [str((repo / ".git").resolve())]


def test_compute_git_sandbox_writable_roots_worktree_relative(tmp_path: Path):
    repo = tmp_path / "repo"
    wt = repo / ".worktrees" / "agent2"
    gitdir = repo / ".git" / "worktrees" / "agent2"
    gitdir.mkdir(parents=True)
    wt.mkdir(parents=True)
    (wt / ".git").write_text("gitdir: ../../.git/worktrees/agent2\n")

    roots = CodexAppServerBackend._compute_git_sandbox_writable_roots(str(wt))
    assert roots == [str((repo / ".git").resolve())]
