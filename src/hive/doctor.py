"""Hive doctor: SQL-based invariant checks for detecting system inconsistencies.

Each check is a standalone function that queries the database and returns a CheckResult
indicating whether the invariant holds (ok), has warnings (warn), or has failures (fail).
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal, Optional

from .config import Config
from .db import Database


@dataclass
class WorktreeInfo:
    """Parsed representation of a single git worktree."""

    path: str
    commit: str
    branch: str | None  # None for detached HEAD or bare worktrees
    is_bare: bool


def _parse_worktrees(raw_output: str) -> list[WorktreeInfo]:
    """Parse `git worktree list --porcelain` output into structured data."""
    worktrees: list[WorktreeInfo] = []
    current_path: str | None = None
    current_commit = ""
    current_branch: str | None = None
    current_is_bare = False

    for line in raw_output.strip().split("\n"):
        if line.startswith("worktree "):
            if current_path is not None:
                worktrees.append(WorktreeInfo(path=current_path, commit=current_commit, branch=current_branch, is_bare=current_is_bare))
            current_path = line[9:]
            current_commit = ""
            current_branch = None
            current_is_bare = False
        elif line.startswith("HEAD "):
            current_commit = line[5:]
        elif line.startswith("branch "):
            current_branch = line[7:]
        elif line == "bare":
            current_is_bare = True

    if current_path is not None:
        worktrees.append(WorktreeInfo(path=current_path, commit=current_commit, branch=current_branch, is_bare=current_is_bare))

    return worktrees


@dataclass
class CheckResult:
    """Result of a single invariant check."""

    id: str
    status: Literal["ok", "warn", "fail"]
    description: str
    details: list[dict[str, Any]]  # Affected rows for verbose output
    fix: Optional[Callable[[Database], None]] = None  # Optional auto-fix function


def check_inv1_exhausted_retry_budget(db: Database) -> CheckResult:
    """INV-1: Detect open issues with exhausted retry budget.

    Open issues where retry event count >= MAX_RETRIES should be escalated or failed.
    """
    query = """
        SELECT
            i.id,
            i.title,
            i.status,
            COUNT(e.id) as retry_count
        FROM issues i
        LEFT JOIN events e ON i.id = e.issue_id AND e.event_type = 'retry'
        WHERE i.status = 'open'
        GROUP BY i.id
        HAVING retry_count >= ?
    """
    cursor = db.conn.execute(query, (Config.MAX_RETRIES,))
    rows = [dict(row) for row in cursor.fetchall()]

    if rows:
        return CheckResult(
            id="INV-1",
            status="fail",
            description=f"Found {len(rows)} open issue(s) with exhausted retry budget (>= {Config.MAX_RETRIES})",
            details=rows,
        )
    return CheckResult(
        id="INV-1",
        status="ok",
        description="No open issues with exhausted retry budget",
        details=[],
    )


def check_inv2_assignee_status_consistency(db: Database) -> CheckResult:
    """INV-2: Detect assignee/status consistency violations.

    - Open issues should have null assignee
    - In-progress issues should have non-null assignee
    """
    # Check 1: Open issues with non-null assignee
    cursor = db.conn.execute(
        """
        SELECT id, title, status, assignee
        FROM issues
        WHERE status = 'open' AND assignee IS NOT NULL
        """
    )
    open_with_assignee = [dict(row) for row in cursor.fetchall()]

    # Check 2: In-progress issues with null assignee
    cursor = db.conn.execute(
        """
        SELECT id, title, status, assignee
        FROM issues
        WHERE status = 'in_progress' AND assignee IS NULL
        """
    )
    in_progress_without_assignee = [dict(row) for row in cursor.fetchall()]

    all_violations = open_with_assignee + in_progress_without_assignee

    if all_violations:
        return CheckResult(
            id="INV-2",
            status="warn",
            description=f"Found {len(all_violations)} assignee/status consistency violation(s)",
            details=all_violations,
        )
    return CheckResult(
        id="INV-2",
        status="ok",
        description="All issues have consistent assignee/status",
        details=[],
    )


def check_inv3_unbounded_loops(db: Database) -> CheckResult:
    """INV-3: Detect unbounded loops (excessive agent switches).

    Issues with agent count > MAX_RETRIES + MAX_AGENT_SWITCHES + margin (5)
    indicate potential infinite retry/switch loops.
    """
    margin = 5
    threshold = Config.MAX_RETRIES + Config.MAX_AGENT_SWITCHES + margin

    query = """
        SELECT
            i.id,
            i.title,
            i.status,
            COUNT(DISTINCT e.agent_id) as agent_count
        FROM issues i
        LEFT JOIN events e ON i.id = e.issue_id AND e.agent_id IS NOT NULL
        WHERE i.status NOT IN ('done', 'finalized', 'canceled')
        GROUP BY i.id
        HAVING agent_count > ?
    """
    cursor = db.conn.execute(query, (threshold,))
    rows = [dict(row) for row in cursor.fetchall()]

    if rows:
        return CheckResult(
            id="INV-3",
            status="fail",
            description=f"Found {len(rows)} issue(s) with excessive agent switches (> {threshold})",
            details=rows,
        )
    return CheckResult(
        id="INV-3",
        status="ok",
        description="No issues with excessive agent switches",
        details=[],
    )


def check_inv5_retry_count_disagreement(db: Database) -> CheckResult:
    """INV-5: Detect retry count vs expected state disagreement.

    Issues with retry events but still in 'open' status when they should be
    escalated, or issues in 'failed' without sufficient retry events.
    """
    # Check 1: Issues with >= MAX_RETRIES but not failed/escalated
    query = """
        SELECT
            i.id,
            i.title,
            i.status,
            COUNT(e.id) as retry_count
        FROM issues i
        LEFT JOIN events e ON i.id = e.issue_id AND e.event_type = 'retry'
        GROUP BY i.id
        HAVING retry_count >= ? AND i.status NOT IN ('failed', 'escalated', 'done', 'finalized', 'canceled')
    """
    cursor = db.conn.execute(query, (Config.MAX_RETRIES,))
    rows = [dict(row) for row in cursor.fetchall()]

    if rows:
        return CheckResult(
            id="INV-5",
            status="fail",
            description=f"Found {len(rows)} issue(s) with retry count >= {Config.MAX_RETRIES} but not failed/escalated",
            details=rows,
        )
    return CheckResult(
        id="INV-5",
        status="ok",
        description="Retry counts match expected states",
        details=[],
    )


def _fix_inv7_stuck_merges(db: Database) -> None:
    """Fix INV-7: Reset stuck merge_queue entries from 'running' to 'queued'."""
    # Find stuck merges
    query = """
        SELECT id, issue_id
        FROM merge_queue
        WHERE status = 'running'
          AND julianday('now') - julianday(enqueued_at) > (30.0 / 24.0 / 60.0)
    """
    cursor = db.conn.execute(query)
    rows = cursor.fetchall()

    for row in rows:
        merge_id, issue_id = row
        # Reset to queued
        db.conn.execute(
            "UPDATE merge_queue SET status = 'queued', completed_at = NULL WHERE id = ?",
            (merge_id,),
        )
        # Log the fix
        db.log_event(
            issue_id,
            None,
            "doctor_fix",
            {"check": "INV-7", "action": "reset_merge_to_queued", "merge_id": merge_id},
        )
    db.conn.commit()


def check_inv7_stuck_merges(db: Database) -> CheckResult:
    """INV-7: Detect stuck merge queue entries.

    Merge queue entries in 'running' status for > 30 minutes indicate stalled
    merge/test/finalization processes.
    """
    query = """
        SELECT
            mq.id,
            mq.issue_id,
            mq.status,
            mq.enqueued_at,
            i.title,
            CAST((julianday('now') - julianday(mq.enqueued_at)) * 24 * 60 AS INTEGER) as minutes_running
        FROM merge_queue mq
        JOIN issues i ON mq.issue_id = i.id
        WHERE mq.status = 'running'
          AND julianday('now') - julianday(mq.enqueued_at) > (30.0 / 24.0 / 60.0)
    """
    cursor = db.conn.execute(query)
    rows = [dict(row) for row in cursor.fetchall()]

    if rows:
        return CheckResult(
            id="INV-7",
            status="warn",
            description=f"Found {len(rows)} merge(s) stuck in 'running' for > 30 minutes",
            details=rows,
            fix=_fix_inv7_stuck_merges,
        )
    return CheckResult(
        id="INV-7",
        status="ok",
        description="No stuck merges detected",
        details=[],
    )


def _fix_inv6_orphaned_agents(db: Database) -> None:
    """Fix INV-6: Mark orphaned agents as 'failed' and clear their current_issue."""
    # Find orphaned agents (active/working status with no worktree on disk)
    cursor = db.conn.execute(
        """
        SELECT id, current_issue, worktree
        FROM agents
        WHERE status IN ('active', 'working')
        """
    )
    rows = cursor.fetchall()

    for row in rows:
        agent_id, current_issue, worktree = row
        if worktree and not Path(worktree).exists():
            # Agent is orphaned - mark as failed
            db.conn.execute(
                "UPDATE agents SET status = 'failed', current_issue = NULL WHERE id = ?",
                (agent_id,),
            )
            # Log the fix
            db.log_event(
                current_issue,
                agent_id,
                "doctor_fix",
                {"check": "INV-6", "action": "mark_agent_failed", "agent_id": agent_id},
            )
    db.conn.commit()


def check_inv6_orphaned_agents(db: Database) -> CheckResult:
    """INV-6: Detect orphaned agents.

    Agents with 'active' or 'working' status but no worktree on disk are orphaned.
    This happens when the agent process crashes or is killed.
    """
    cursor = db.conn.execute(
        """
        SELECT id, name, status, worktree, current_issue
        FROM agents
        WHERE status IN ('active', 'working')
        """
    )
    rows = [dict(row) for row in cursor.fetchall()]

    orphaned = []
    for row in rows:
        worktree = row.get("worktree")
        if worktree and not Path(worktree).exists():
            orphaned.append(row)

    if orphaned:
        return CheckResult(
            id="INV-6",
            status="fail",
            description=f"Found {len(orphaned)} orphaned agent(s) with missing worktrees",
            details=orphaned,
            fix=_fix_inv6_orphaned_agents,
        )
    return CheckResult(
        id="INV-6",
        status="ok",
        description="No orphaned agents detected",
        details=[],
    )


def _fix_inv8_ghost_worktrees(db: Database) -> None:
    """Fix INV-8: Remove ghost worktrees with no corresponding active agent."""
    # Get git worktree list
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError:
        return  # Can't get worktree list, skip fix

    worktrees = _parse_worktrees(result.stdout)

    # Get active agents from DB
    cursor = db.conn.execute("SELECT worktree FROM agents WHERE status IN ('active', 'working', 'idle')")
    active_worktrees = {row[0] for row in cursor.fetchall() if row[0]}

    # Find ghost worktrees (on disk but not in DB)
    for wt in worktrees:
        # Skip bare/detached worktrees (no branch)
        if wt.branch is None:
            continue

        # Skip if worktree has an active agent
        if wt.path in active_worktrees:
            continue

        # This is a ghost - remove it
        try:
            subprocess.run(
                ["git", "worktree", "remove", "--force", wt.path],
                capture_output=True,
                check=True,
            )
            # Log the fix
            db.log_event(
                None,
                None,
                "doctor_fix",
                {"check": "INV-8", "action": "remove_ghost_worktree", "path": wt.path},
            )
        except subprocess.CalledProcessError:
            pass  # Skip if removal fails

    db.conn.commit()


def check_inv8_ghost_worktrees(db: Database) -> CheckResult:
    """INV-8: Detect ghost worktrees.

    Git worktrees on disk with no corresponding active agent in DB.
    This can happen when agent cleanup fails or DB state is lost.
    """
    # Get git worktree list
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        return CheckResult(
            id="INV-8",
            status="warn",
            description=f"Failed to get git worktree list: {e}",
            details=[],
        )

    worktrees = _parse_worktrees(result.stdout)

    # Get active agents from DB
    cursor = db.conn.execute("SELECT worktree FROM agents WHERE status IN ('active', 'working', 'idle')")
    active_worktrees = {row[0] for row in cursor.fetchall() if row[0]}

    # Find ghost worktrees (on disk but not in DB)
    ghosts = []
    for wt in worktrees:
        # Skip bare/detached worktrees (no branch)
        if wt.branch is None:
            continue

        # Skip if worktree has an active agent
        if wt.path in active_worktrees:
            continue

        ghosts.append({"path": wt.path, "branch": wt.branch})

    if ghosts:
        return CheckResult(
            id="INV-8",
            status="warn",
            description=f"Found {len(ghosts)} ghost worktree(s) with no active agent",
            details=ghosts,
            fix=_fix_inv8_ghost_worktrees,
        )
    return CheckResult(
        id="INV-8",
        status="ok",
        description="No ghost worktrees detected",
        details=[],
    )


def _fix_inv9_project_name_mismatches(db: Database) -> None:
    """Fix INV-9: Normalize 'org/repo' project names in the projects table to bare 'repo' form."""
    cursor = db.conn.execute("SELECT name, path FROM projects WHERE name LIKE '%/%'")
    rows = cursor.fetchall()
    for row in rows:
        old_name, path = row[0], row[1]
        new_name = old_name.rsplit("/", 1)[-1]
        # Only rename if the bare name isn't already registered to a different path
        existing = db.conn.execute("SELECT path FROM projects WHERE name = ?", (new_name,)).fetchone()
        if existing is None:
            db.conn.execute("UPDATE projects SET name = ? WHERE name = ?", (new_name, old_name))
        elif existing[0] == path:
            # Same path — safe to remove the old "org/repo" duplicate
            db.conn.execute("DELETE FROM projects WHERE name = ?", (old_name,))
        # else: collision — leave as-is for human review
    db.conn.commit()


def check_inv9_project_name_mismatch(db: Database) -> CheckResult:
    """INV-9: Detect project name inconsistencies between projects and issues tables.

    Checks for:
    - Projects table entries with names containing "/" (stale "org/repo" form).
    - Issues whose project field does not match any name in the projects table
      (when the projects table is non-empty, indicating the DB is project-aware).
    """
    details: list[dict] = []

    # Check 1: Stale "org/repo" names in the projects table
    cursor = db.conn.execute("SELECT name, path FROM projects WHERE name LIKE '%/%'")
    for row in cursor.fetchall():
        details.append({"type": "slash_in_project_name", "name": row[0], "path": row[1]})

    # Check 2: Issues whose project doesn't match any projects table entry
    # Only meaningful when the projects table is populated
    project_count = db.conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    if project_count > 0:
        cursor = db.conn.execute(
            """
            SELECT DISTINCT i.project
            FROM issues i
            WHERE i.project != ''
              AND i.project NOT IN (SELECT name FROM projects)
            """
        )
        for row in cursor.fetchall():
            details.append({"type": "issue_project_not_registered", "project": row[0]})

    if details:
        slash_count = sum(1 for d in details if d["type"] == "slash_in_project_name")
        orphan_count = sum(1 for d in details if d["type"] == "issue_project_not_registered")
        parts = []
        if slash_count:
            parts.append(f"{slash_count} project(s) with 'org/repo' names")
        if orphan_count:
            parts.append(f"{orphan_count} issue project(s) not in projects table")
        return CheckResult(
            id="INV-9",
            status="fail",
            description="Project name mismatch: " + "; ".join(parts),
            details=details,
            fix=_fix_inv9_project_name_mismatches,
        )
    return CheckResult(
        id="INV-9",
        status="ok",
        description="Project names are consistent between issues and projects tables",
        details=[],
    )


# Registry of all checks
ALL_CHECKS = [
    check_inv1_exhausted_retry_budget,
    check_inv2_assignee_status_consistency,
    check_inv3_unbounded_loops,
    check_inv5_retry_count_disagreement,
    check_inv6_orphaned_agents,
    check_inv7_stuck_merges,
    check_inv8_ghost_worktrees,
    check_inv9_project_name_mismatch,
]


def run_all_checks(db: Database) -> list[CheckResult]:
    """Run all invariant checks and return results.

    Args:
        db: Connected Database instance

    Returns:
        List of CheckResult objects, one per check
    """
    results = []
    for check_fn in ALL_CHECKS:
        result = check_fn(db)
        results.append(result)
    return results
