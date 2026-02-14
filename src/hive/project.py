"""Project auto-detection for Hive.

Walks up from cwd to find the nearest .git/ directory, then resolves the
project name from (in priority order):
  1. .hive.toml [project] name
  2. git remote origin URL
  3. directory name
"""

import subprocess
from pathlib import Path


def _parse_repo_name(remote_url: str) -> str | None:
    """Extract repository name from a git remote URL.

    Handles both SSH (git@...) and HTTPS (https://...) formats.
    """
    url = remote_url.strip().rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    # git@github.com:user/repo  or  https://github.com/user/repo
    for sep in (":", "/"):
        if sep in url:
            name = url.rsplit(sep, 1)[-1]
            if name:
                return name
    return None


def _git_remote_name(project_root: Path) -> str | None:
    """Get the repo name from git remote origin."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return _parse_repo_name(result.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def detect_project(cwd: Path | None = None) -> tuple[Path, str]:
    """Detect the current project root and name.

    Walks up from *cwd* (default: ``Path.cwd()``) looking for a ``.git/``
    directory.  Once found, the project name is resolved from ``.hive.toml``,
    the git remote origin URL, or the directory name (in that order).

    Returns:
        ``(project_path, project_name)``

    Raises:
        SystemExit: if no ``.git/`` directory is found.
    """
    import sys

    start = Path(cwd) if cwd else Path.cwd()
    current = start.resolve()

    # Walk up to find .git/
    while True:
        if (current / ".git").exists():
            break
        parent = current.parent
        if parent == current:
            print(f"fatal: not a git repository (searched up from {start})", file=sys.stderr)
            sys.exit(128)
        current = parent

    project_root = current

    # 1. Try .hive.toml
    hive_toml = project_root / ".hive.toml"
    if hive_toml.exists():
        import tomllib

        with open(hive_toml, "rb") as f:
            data = tomllib.load(f)
        name = (data.get("project") or {}).get("name")
        if name:
            return project_root, name

    # 2. Try git remote origin
    remote_name = _git_remote_name(project_root)
    if remote_name:
        return project_root, remote_name

    # 3. Fallback to directory name
    return project_root, project_root.name
