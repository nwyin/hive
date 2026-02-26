"""Tests for hive doctor module."""

import tempfile

from hive.config import Config
from hive.doctor import (
    WorktreeInfo,
    _parse_worktrees,
    check_inv1_exhausted_retry_budget,
    check_inv2_assignee_status_consistency,
    check_inv3_unbounded_loops,
    check_inv5_retry_count_disagreement,
    check_inv6_orphaned_agents,
    check_inv7_stuck_merges,
    check_inv8_ghost_worktrees,
    run_all_checks,
)

# ── _parse_worktrees tests ─────────────────────────────────────────────

_MAIN_ONLY = """\
worktree /repo
HEAD abc1234567890abcdef1234567890abcdef123456
branch refs/heads/main
"""

_MAIN_PLUS_AGENTS = """\
worktree /repo
HEAD abc1234567890abcdef1234567890abcdef123456
branch refs/heads/main

worktree /repo/.worktrees/worker-aaa
HEAD def1234567890abcdef1234567890abcdef123456
branch refs/heads/agent/worker-aaa

worktree /repo/.worktrees/worker-bbb
HEAD fed1234567890abcdef1234567890abcdef123456
branch refs/heads/agent/worker-bbb
"""

_WITH_DETACHED = """\
worktree /repo
HEAD abc1234567890abcdef1234567890abcdef123456
branch refs/heads/main

worktree /repo/.worktrees/detached
HEAD bbb1234567890abcdef1234567890abcdef123456
detached
"""

_WITH_BARE = """\
worktree /repo
HEAD abc1234567890abcdef1234567890abcdef123456
bare
"""


def test_parse_worktrees_main_only():
    """INV-1: Parse output with just the main worktree."""
    result = _parse_worktrees(_MAIN_ONLY)
    assert len(result) == 1
    wt = result[0]
    assert wt.path == "/repo"
    assert wt.commit == "abc1234567890abcdef1234567890abcdef123456"
    assert wt.branch == "refs/heads/main"
    assert wt.is_bare is False


def test_parse_worktrees_multiple_agent_branches():
    """INV-1: Parse output with main + multiple agent worktrees."""
    result = _parse_worktrees(_MAIN_PLUS_AGENTS)
    assert len(result) == 3

    main = result[0]
    assert main.branch == "refs/heads/main"
    assert main.is_bare is False

    agent_paths = [wt.path for wt in result[1:]]
    assert "/repo/.worktrees/worker-aaa" in agent_paths
    assert "/repo/.worktrees/worker-bbb" in agent_paths

    for wt in result[1:]:
        assert wt.branch is not None
        assert wt.branch.startswith("refs/heads/agent/")


def test_parse_worktrees_detached_head():
    """INV-2: Detached HEAD worktree has branch=None and is_bare=False."""
    result = _parse_worktrees(_WITH_DETACHED)
    assert len(result) == 2
    detached = next(wt for wt in result if "detached" in wt.path)
    assert detached.branch is None
    assert detached.is_bare is False
    assert detached.commit == "bbb1234567890abcdef1234567890abcdef123456"


def test_parse_worktrees_bare():
    """INV-2: Bare worktree has branch=None and is_bare=True."""
    result = _parse_worktrees(_WITH_BARE)
    assert len(result) == 1
    wt = result[0]
    assert wt.branch is None
    assert wt.is_bare is True


def test_parse_worktrees_empty_output():
    """Parse empty output returns empty list without error."""
    result = _parse_worktrees("")
    assert result == []


def test_parse_worktrees_returns_worktreeinfo_objects():
    """Parsed items are WorktreeInfo dataclass instances."""
    result = _parse_worktrees(_MAIN_PLUS_AGENTS)
    for wt in result:
        assert isinstance(wt, WorktreeInfo)


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


def test_inv6_orphaned_agents_ok(temp_db):
    """Test INV-6: No orphaned agents when worktrees exist."""
    # Create agent with a real temporary worktree
    with tempfile.TemporaryDirectory() as tmpdir:
        agent_id = temp_db.create_agent("test-agent")
        temp_db.conn.execute(
            "UPDATE agents SET status = 'active', worktree = ? WHERE id = ?",
            (tmpdir, agent_id),
        )
        temp_db.conn.commit()

        result = check_inv6_orphaned_agents(temp_db)
        assert result.status == "ok"
        assert len(result.details) == 0


def test_inv6_orphaned_agents_fail(temp_db):
    """Test INV-6: Detect agent with missing worktree."""
    # Create agent with non-existent worktree
    agent_id = temp_db.create_agent("orphaned-agent")
    issue_id = temp_db.create_issue("Test issue", project="test")

    temp_db.conn.execute(
        "UPDATE agents SET status = 'working', current_issue = ?, worktree = ? WHERE id = ?",
        (issue_id, "/nonexistent/worktree/path", agent_id),
    )
    temp_db.conn.commit()

    result = check_inv6_orphaned_agents(temp_db)
    assert result.status == "fail"
    assert len(result.details) == 1
    assert result.details[0]["id"] == agent_id
    assert result.details[0]["worktree"] == "/nonexistent/worktree/path"


def test_inv6_ignores_idle_agents(temp_db):
    """Test INV-6: Ignore idle agents even if worktree missing."""
    # Create idle agent with missing worktree (should be ignored)
    agent_id = temp_db.create_agent("idle-agent")
    temp_db.conn.execute(
        "UPDATE agents SET worktree = ? WHERE id = ?",
        ("/nonexistent/worktree", agent_id),
    )
    temp_db.conn.commit()

    result = check_inv6_orphaned_agents(temp_db)
    assert result.status == "ok"


def test_inv6_fix_marks_agents_failed(temp_db):
    """Test INV-6 fix: Mark orphaned agents as failed."""
    # Create orphaned agent
    agent_id = temp_db.create_agent("orphaned-agent")
    issue_id = temp_db.create_issue("Test issue", project="test")

    temp_db.conn.execute(
        "UPDATE agents SET status = 'working', current_issue = ?, worktree = ? WHERE id = ?",
        (issue_id, "/nonexistent/worktree", agent_id),
    )
    temp_db.conn.commit()

    # Run check and apply fix
    result = check_inv6_orphaned_agents(temp_db)
    assert result.status == "fail"
    assert result.fix is not None

    result.fix(temp_db)

    # Verify agent is now failed with no current_issue
    cursor = temp_db.conn.execute(
        "SELECT status, current_issue FROM agents WHERE id = ?",
        (agent_id,),
    )
    row = cursor.fetchone()
    assert row[0] == "failed"
    assert row[1] is None

    # Verify event was logged
    events = temp_db.get_events(issue_id=issue_id, event_type="doctor_fix")
    assert len(events) > 0


def test_inv7_fix_resets_stuck_merges(temp_db):
    """Test INV-7 fix: Reset stuck merge queue entries."""
    issue_id = temp_db.create_issue("Stuck merge", project="test")

    # Create stuck merge
    temp_db.conn.execute(
        """
        INSERT INTO merge_queue (issue_id, project, worktree, branch_name, status, enqueued_at)
        VALUES (?, 'test', '/tmp/worktree', 'test-branch', 'running', datetime('now', '-35 minutes'))
        """,
        (issue_id,),
    )
    temp_db.conn.commit()

    # Run check and apply fix
    result = check_inv7_stuck_merges(temp_db)
    assert result.status == "warn"
    assert result.fix is not None

    result.fix(temp_db)

    # Verify merge is now queued
    cursor = temp_db.conn.execute(
        "SELECT status FROM merge_queue WHERE issue_id = ?",
        (issue_id,),
    )
    row = cursor.fetchone()
    assert row[0] == "queued"

    # Verify event was logged
    events = temp_db.get_events(issue_id=issue_id, event_type="doctor_fix")
    assert len(events) > 0


def test_inv8_ghost_worktrees_ok(temp_db):
    """Test INV-8: No ghost worktrees when all worktrees have agents."""
    # This test assumes we're in a git repo
    # If git worktree list fails, check should warn, not fail
    result = check_inv8_ghost_worktrees(temp_db)
    # Should be ok or warn (if git command fails)
    assert result.status in ("ok", "warn")


def test_run_all_checks(temp_db):
    """Test run_all_checks returns list of CheckResults."""
    results = run_all_checks(temp_db)

    # Should return results for all checks (now 7 checks)
    assert len(results) == 7

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
