"""Hive doctor: SQL-based invariant checks for detecting system inconsistencies.

Each check is a standalone function that queries the database and returns a CheckResult
indicating whether the invariant holds (ok), has warnings (warn), or has failures (fail).
"""

from dataclasses import dataclass
from typing import Any, Literal

from .config import Config
from .db import Database


@dataclass
class CheckResult:
    """Result of a single invariant check."""

    id: str
    status: Literal["ok", "warn", "fail"]
    description: str
    details: list[dict[str, Any]]  # Affected rows for verbose output


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
        )
    return CheckResult(
        id="INV-7",
        status="ok",
        description="No stuck merges detected",
        details=[],
    )


# Registry of all checks
ALL_CHECKS = [
    check_inv1_exhausted_retry_budget,
    check_inv2_assignee_status_consistency,
    check_inv3_unbounded_loops,
    check_inv5_retry_count_disagreement,
    check_inv7_stuck_merges,
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
