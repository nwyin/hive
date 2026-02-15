"""Tests for hive doctor module."""

from hive.config import Config
from hive.doctor import (
    check_inv1_exhausted_retry_budget,
    check_inv2_assignee_status_consistency,
    check_inv3_unbounded_loops,
    check_inv5_retry_count_disagreement,
    check_inv7_stuck_merges,
    run_all_checks,
)


def test_inv1_exhausted_retry_budget_ok(temp_db):
    """Test INV-1: No open issues with exhausted retry budget."""
    # Create an open issue with fewer retries than threshold
    issue_id = temp_db.create_issue("Test issue", project="test")
    for _ in range(Config.MAX_RETRIES - 1):
        temp_db.log_event(issue_id, None, "retry", {})

    result = check_inv1_exhausted_retry_budget(temp_db)
    assert result.status == "ok"
    assert len(result.details) == 0


def test_inv1_exhausted_retry_budget_fail(temp_db):
    """Test INV-1: Detect open issue with exhausted retry budget."""
    # Create an open issue with >= MAX_RETRIES
    issue_id = temp_db.create_issue("Stuck issue", project="test")
    for _ in range(Config.MAX_RETRIES):
        temp_db.log_event(issue_id, None, "retry", {})

    result = check_inv1_exhausted_retry_budget(temp_db)
    assert result.status == "fail"
    assert len(result.details) == 1
    assert result.details[0]["id"] == issue_id
    assert result.details[0]["retry_count"] >= Config.MAX_RETRIES


def test_inv1_ignores_closed_issues(temp_db):
    """Test INV-1: Ignore issues that are not open."""
    # Create a done issue with many retries (should be ignored)
    issue_id = temp_db.create_issue("Done issue", project="test")
    temp_db.update_issue_status(issue_id, "done")
    for _ in range(Config.MAX_RETRIES + 5):
        temp_db.log_event(issue_id, None, "retry", {})

    result = check_inv1_exhausted_retry_budget(temp_db)
    assert result.status == "ok"


def test_inv2_assignee_status_consistency_ok(temp_db):
    """Test INV-2: Consistent assignee/status pairings."""
    # Open issue with null assignee (correct)
    temp_db.create_issue("Open task", project="test")

    # In-progress issue with assignee (correct)
    issue_id = temp_db.create_issue("Working task", project="test")
    agent_id = temp_db.create_agent("test-agent")
    temp_db.claim_issue(issue_id, agent_id)

    result = check_inv2_assignee_status_consistency(temp_db)
    assert result.status == "ok"
    assert len(result.details) == 0


def test_inv2_open_with_assignee_warn(temp_db):
    """Test INV-2: Detect open issue with non-null assignee."""
    issue_id = temp_db.create_issue("Bad issue", project="test")
    agent_id = temp_db.create_agent("test-agent")

    # Manually set assignee without claiming (simulates inconsistency)
    temp_db.conn.execute(
        "UPDATE issues SET assignee = ? WHERE id = ?",
        (agent_id, issue_id),
    )
    temp_db.conn.commit()

    result = check_inv2_assignee_status_consistency(temp_db)
    assert result.status == "warn"
    assert len(result.details) == 1
    assert result.details[0]["id"] == issue_id


def test_inv2_in_progress_without_assignee_warn(temp_db):
    """Test INV-2: Detect in_progress issue with null assignee."""
    issue_id = temp_db.create_issue("Orphaned issue", project="test")

    # Manually set status to in_progress without assignee
    temp_db.conn.execute(
        "UPDATE issues SET status = 'in_progress' WHERE id = ?",
        (issue_id,),
    )
    temp_db.conn.commit()

    result = check_inv2_assignee_status_consistency(temp_db)
    assert result.status == "warn"
    assert len(result.details) == 1
    assert result.details[0]["id"] == issue_id


def test_inv3_unbounded_loops_ok(temp_db):
    """Test INV-3: No issues with excessive agent switches."""
    issue_id = temp_db.create_issue("Normal issue", project="test")

    # Create events from a reasonable number of agents
    for i in range(Config.MAX_RETRIES + Config.MAX_AGENT_SWITCHES):
        agent_id = temp_db.create_agent(f"agent-{i}")
        temp_db.log_event(issue_id, agent_id, "claimed", {})

    result = check_inv3_unbounded_loops(temp_db)
    assert result.status == "ok"


def test_inv3_unbounded_loops_fail(temp_db):
    """Test INV-3: Detect issue with excessive agent switches."""
    issue_id = temp_db.create_issue("Loop issue", project="test")

    # Create events from too many agents (threshold + margin + 1)
    threshold = Config.MAX_RETRIES + Config.MAX_AGENT_SWITCHES + 5
    for i in range(threshold + 2):
        agent_id = temp_db.create_agent(f"agent-{i}")
        temp_db.log_event(issue_id, agent_id, "claimed", {})

    result = check_inv3_unbounded_loops(temp_db)
    assert result.status == "fail"
    assert len(result.details) == 1
    assert result.details[0]["id"] == issue_id
    assert result.details[0]["agent_count"] > threshold


def test_inv5_retry_count_disagreement_ok(temp_db):
    """Test INV-5: Retry counts match expected states."""
    # Issue with no retries in open state (ok)
    temp_db.create_issue("Clean issue", project="test")

    # Issue with retries but in failed state (ok)
    issue_id = temp_db.create_issue("Failed issue", project="test")
    for _ in range(Config.MAX_RETRIES):
        temp_db.log_event(issue_id, None, "retry", {})
    temp_db.update_issue_status(issue_id, "failed")

    result = check_inv5_retry_count_disagreement(temp_db)
    assert result.status == "ok"


def test_inv5_retry_count_disagreement_fail(temp_db):
    """Test INV-5: Detect issue with retries but not failed/escalated."""
    issue_id = temp_db.create_issue("Should be failed", project="test")

    # Add >= MAX_RETRIES but keep in in_progress
    for _ in range(Config.MAX_RETRIES):
        temp_db.log_event(issue_id, None, "retry", {})
    temp_db.update_issue_status(issue_id, "in_progress")

    result = check_inv5_retry_count_disagreement(temp_db)
    assert result.status == "fail"
    assert len(result.details) == 1
    assert result.details[0]["id"] == issue_id


def test_inv7_stuck_merges_ok(temp_db):
    """Test INV-7: No stuck merges."""
    # Create a recent merge queue entry in running state
    issue_id = temp_db.create_issue("Merging issue", project="test")
    temp_db.conn.execute(
        """
        INSERT INTO merge_queue (issue_id, project, worktree, branch_name, status, enqueued_at)
        VALUES (?, 'test', '/tmp/worktree', 'test-branch', 'running', datetime('now'))
        """,
        (issue_id,),
    )
    temp_db.conn.commit()

    result = check_inv7_stuck_merges(temp_db)
    assert result.status == "ok"


def test_inv7_stuck_merges_warn(temp_db):
    """Test INV-7: Detect merge stuck for > 30 minutes."""
    issue_id = temp_db.create_issue("Stuck merge", project="test")

    # Insert merge queue entry from 35 minutes ago
    temp_db.conn.execute(
        """
        INSERT INTO merge_queue (issue_id, project, worktree, branch_name, status, enqueued_at)
        VALUES (?, 'test', '/tmp/worktree', 'test-branch', 'running', datetime('now', '-35 minutes'))
        """,
        (issue_id,),
    )
    temp_db.conn.commit()

    result = check_inv7_stuck_merges(temp_db)
    assert result.status == "warn"
    assert len(result.details) == 1
    assert result.details[0]["issue_id"] == issue_id
    assert result.details[0]["minutes_running"] > 30


def test_inv7_ignores_queued_merges(temp_db):
    """Test INV-7: Ignore queued merges (only check running)."""
    issue_id = temp_db.create_issue("Queued merge", project="test")

    # Old queued entry (not running, should be ignored)
    temp_db.conn.execute(
        """
        INSERT INTO merge_queue (issue_id, project, worktree, branch_name, status, enqueued_at)
        VALUES (?, 'test', '/tmp/worktree', 'test-branch', 'queued', datetime('now', '-60 minutes'))
        """,
        (issue_id,),
    )
    temp_db.conn.commit()

    result = check_inv7_stuck_merges(temp_db)
    assert result.status == "ok"


def test_run_all_checks(temp_db):
    """Test run_all_checks returns list of CheckResults."""
    results = run_all_checks(temp_db)

    # Should return results for all checks
    assert len(results) == 5

    # All should be CheckResult objects with required fields
    for result in results:
        assert hasattr(result, "id")
        assert hasattr(result, "status")
        assert hasattr(result, "description")
        assert hasattr(result, "details")
        assert result.status in ("ok", "warn", "fail")
        assert isinstance(result.details, list)


def test_run_all_checks_with_failures(temp_db):
    """Test run_all_checks when some checks fail."""
    # Create conditions that trigger multiple failures
    issue1 = temp_db.create_issue("Exhausted retries", project="test")
    for _ in range(Config.MAX_RETRIES):
        temp_db.log_event(issue1, None, "retry", {})

    issue2 = temp_db.create_issue("Orphaned", project="test")
    temp_db.conn.execute(
        "UPDATE issues SET status = 'in_progress' WHERE id = ?",
        (issue2,),
    )
    temp_db.conn.commit()

    results = run_all_checks(temp_db)

    # Check that we have some failures/warnings
    statuses = [r.status for r in results]
    assert "fail" in statuses or "warn" in statuses

    # INV-1 should fail
    inv1_result = next(r for r in results if r.id == "INV-1")
    assert inv1_result.status == "fail"

    # INV-2 should warn
    inv2_result = next(r for r in results if r.id == "INV-2")
    assert inv2_result.status == "warn"
