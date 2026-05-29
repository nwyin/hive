"""Tests for CLI interface."""

import json
import subprocess
import unittest.mock

import pytest

from hive.cli import HiveCLI, toon
from hive.cli.helpers import _enrich_agents_with_issues
from hive.cli.typer_app import run as run_typer


# ── _enrich_agents_with_issues helper ─────────────────────────────────────────


def test_enrich_agents_returns_worker_dicts(temp_db, tmp_path):
    """Helper returns one dict per agent with name/issue_id/issue_title keys."""
    issue_id = temp_db.create_issue("Fix the bug", project=tmp_path.name)
    agent_id = temp_db.create_agent(name="worker-abc", project=tmp_path.name)
    temp_db.conn.execute("UPDATE agents SET current_issue = ? WHERE id = ?", (issue_id, agent_id))
    temp_db.conn.commit()

    agents = temp_db.get_active_agents(project=tmp_path.name)
    # make agent working so get_active_agents returns it
    temp_db.conn.execute("UPDATE agents SET status = 'working' WHERE id = ?", (agent_id,))
    temp_db.conn.commit()
    agents = temp_db.get_active_agents(project=tmp_path.name)

    result = _enrich_agents_with_issues(temp_db, agents)

    assert len(result) == len(agents)
    worker = result[0]
    assert worker["name"] == "worker-abc"
    assert worker["issue_id"] == issue_id
    assert worker["issue_title"] == "Fix the bug"


def test_enrich_agents_empty_input(temp_db):
    """Helper returns empty list for empty agent list."""
    assert _enrich_agents_with_issues(temp_db, []) == []


def test_enrich_agents_no_current_issue(temp_db):
    """Helper returns empty issue_id and issue_title when agent has no current issue."""
    agent_dict = {"name": "idle-worker", "current_issue": None}
    result = _enrich_agents_with_issues(temp_db, [agent_dict])

    assert result[0]["issue_title"] == ""
    assert result[0]["issue_id"] == ""
    assert result[0]["name"] == "idle-worker"


def test_enrich_agents_missing_issue_in_db(temp_db):
    """Helper gracefully handles an agent whose current_issue no longer exists."""
    agent_dict = {"name": "stale-worker", "current_issue": "w-doesnotexist"}
    result = _enrich_agents_with_issues(temp_db, [agent_dict])

    assert result[0]["issue_title"] == ""
    assert result[0]["issue_id"] == "w-doesnotexist"


def test_cli_create(temp_db, tmp_path):
    """Test creating an issue via CLI."""
    cli = HiveCLI(temp_db, str(tmp_path))

    result = cli.create("Test issue", "Test description", priority=1)
    issue_id = result["id"]

    assert issue_id.startswith("w-")
    assert "issue_id" not in result

    # Verify issue was created
    issue = temp_db.get_issue(issue_id)
    assert issue is not None
    assert issue["title"] == "Test issue"
    assert issue["description"] == "Test description"
    assert issue["priority"] == 1


def test_cli_create_with_depends_on(temp_db, tmp_path, capsys):
    """Test creating an issue with --depends-on wires deps atomically."""
    cli = HiveCLI(temp_db, str(tmp_path))

    # Create a blocker issue first
    blocker_id = cli.create("Blocker", "block desc")["id"]

    # Create a dependent issue with --depends-on
    dependent_id = cli.create("Dependent", "dep desc", depends_on=[blocker_id])["id"]

    # Verify dependency was created
    issue = temp_db.get_issue(dependent_id)
    assert issue["status"] == "open"

    # The dependent should NOT be claimable (blocker is still open)
    agent_id = temp_db.create_agent("test-agent")
    claimed = temp_db.claim_issue(dependent_id, agent_id)
    assert not claimed

    # Resolve blocker, then claim should work
    temp_db.try_transition_issue_status(blocker_id, to_status="finalized")
    claimed = temp_db.claim_issue(dependent_id, agent_id)
    assert claimed


def test_cli_create_with_depends_on_json(temp_db, tmp_path, capsys):
    """Test --depends-on shows in JSON output."""
    cli = HiveCLI(temp_db, str(tmp_path))

    blocker_id = cli.create("Blocker", "desc")["id"]
    capsys.readouterr()  # Clear output from first create

    cli.create("Dependent", "desc", depends_on=[blocker_id])

    captured = capsys.readouterr()
    data = toon.decode(captured.out)
    assert blocker_id in data["depends_on"]


def test_cli_list_issues(temp_db, tmp_path, capsys):
    """Test listing issues via CLI."""
    cli = HiveCLI(temp_db, str(tmp_path))

    # Create some issues
    temp_db.create_issue("Issue 1", priority=1, project=tmp_path.name)
    temp_db.create_issue("Issue 2", priority=2, project=tmp_path.name)
    temp_db.create_issue("Issue 3", priority=3, project=tmp_path.name)

    cli.list_issues()

    data = toon.decode(capsys.readouterr().out)
    assert data["count"] == 3
    assert data["total"] == 3
    titles = {row["title"] for row in data["issues"]}
    assert {"Issue 1", "Issue 2", "Issue 3"} <= titles


def test_cli_list_issues_by_status(temp_db, tmp_path, capsys):
    """Test listing issues filtered by status."""
    cli = HiveCLI(temp_db, str(tmp_path))

    # Create issues with different statuses
    temp_db.create_issue("Open issue", project=tmp_path.name)
    issue2 = temp_db.create_issue("Done issue", project=tmp_path.name)
    temp_db.try_transition_issue_status(issue2, to_status="done")

    cli.list_issues(status="open")

    captured = capsys.readouterr()
    assert "Open issue" in captured.out
    assert "Done issue" not in captured.out


def test_cli_show_issue(temp_db, tmp_path, capsys):
    """Test showing issue details."""
    cli = HiveCLI(temp_db, str(tmp_path))

    issue_id = temp_db.create_issue("Test issue", "Detailed description", priority=1, project=tmp_path.name)

    cli.show(issue_id)

    data = toon.decode(capsys.readouterr().out)
    assert data["id"] == issue_id
    assert data["title"] == "Test issue"
    assert data["description"] == "Detailed description"
    assert data["priority"] == 1


def test_cli_show_issue_json(temp_db, tmp_path, capsys):
    """Test showing issue details with JSON output."""
    cli = HiveCLI(temp_db, str(tmp_path))

    issue_id = temp_db.create_issue("Test issue", "desc", priority=1, project=tmp_path.name)

    cli.show(issue_id)

    captured = capsys.readouterr()
    data = toon.decode(captured.out)
    assert data["id"] == issue_id
    assert data["title"] == "Test issue"
    assert "dependencies" in data
    assert "dependents" in data


def test_cli_status(temp_db, tmp_path, capsys):
    """Test showing status."""
    cli = HiveCLI(temp_db, str(tmp_path))

    # Create some issues
    temp_db.create_issue("Open 1", project=tmp_path.name)
    temp_db.create_issue("Open 2", project=tmp_path.name)
    issue3 = temp_db.create_issue("Done 1", project=tmp_path.name)
    temp_db.try_transition_issue_status(issue3, to_status="done")

    cli.status()

    data = toon.decode(capsys.readouterr().out)
    assert data["issues"]["open"] == 2
    assert data["issues"]["done"] == 1
    assert "ready_queue" in data


def test_cli_status_json(temp_db, tmp_path, capsys):
    """Test showing status with JSON output."""
    cli = HiveCLI(temp_db, str(tmp_path))

    temp_db.create_issue("Open 1", project=tmp_path.name)

    cli.status()

    captured = capsys.readouterr()
    data = toon.decode(captured.out)
    assert "issues" in data
    assert "total_issues" in data
    assert data["project"] == tmp_path.name


def test_cli_status_json_includes_dirty_main_merge_blocker(temp_db, tmp_path, capsys):
    """JSON status should include structured dirty-main merge blocker details."""
    repo = tmp_path / "repo-json"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True, capture_output=True)
    (repo / "README.md").write_text("# Repo\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "branch", "-M", "main"], cwd=repo, check=True, capture_output=True)

    cli = HiveCLI(temp_db, str(repo))
    issue_id = temp_db.create_issue("Queued merge", project=repo.name)
    temp_db.try_transition_issue_status(issue_id, to_status="done")
    temp_db.conn.execute(
        """
        INSERT INTO merge_queue (issue_id, agent_id, project, worktree, branch_name, status)
        VALUES (?, ?, ?, ?, ?, 'queued')
        """,
        (issue_id, "agent-2", repo.name, str(repo / ".worktrees" / "agent-2"), "agent/agent-2"),
    )
    temp_db.conn.commit()
    (repo / "README.md").write_text("# Dirty Repo\n")

    cli.status()
    captured = capsys.readouterr()
    data = toon.decode(captured.out)
    assert data["main_worktree"]["dirty"] is True
    assert data["merge_blockers"]
    assert data["merge_blockers"][0]["type"] == "dirty_main_worktree"


def test_cli_show_via_main_is_toon(temp_db_file, tmp_path, capsys):
    """The real entrypoint emits TOON that matches the method's output."""
    from hive.cli import main

    cli = HiveCLI(temp_db_file, str(tmp_path))
    issue_id = temp_db_file.create_issue("Format test", "desc", priority=3, project=tmp_path.name)

    # Method produces the canonical TOON payload.
    cli.show(issue_id)
    expected = toon.decode(capsys.readouterr().out)

    # `hive show <id>` through the entrypoint produces the same TOON.
    main(["--project", str(tmp_path), "--db", str(temp_db_file.db_path), "show", issue_id])
    data = toon.decode(capsys.readouterr().out)
    assert data == expected
    assert data["id"] == issue_id
    assert data["title"] == "Format test"

    # Verify json format output has correct structure
    assert expected["id"] == issue_id
    assert expected["title"] == "Format test"


def _strip_ansi(s: str) -> str:
    import re

    return re.sub(r"\x1b\[[0-9;]*m", "", s)


def test_main_no_args_prints_help_without_raising(capsys):
    """Running the CLI with no args should show help and exit cleanly."""
    from hive.cli import main

    main([])

    captured = capsys.readouterr()
    out = _strip_ansi(captured.out)
    assert "Usage: hive" in out
    assert "Hive multi-agent orchestrator." in out
    assert "NoArgsIsHelpError" not in out
    assert captured.err == ""


def test_cli_show_issue_with_dependencies(temp_db, tmp_path, capsys):
    """Test showing issue with dependencies."""
    cli = HiveCLI(temp_db, str(tmp_path))

    # Create issues with dependencies
    issue1 = temp_db.create_issue("Dependency", project=tmp_path.name)
    issue2 = temp_db.create_issue("Main task", project=tmp_path.name)

    temp_db.add_dependency(issue2, issue1)

    cli.show(issue2)

    data = toon.decode(capsys.readouterr().out)
    dep_ids = {dep["id"] for dep in data["dependencies"]}
    assert issue1 in dep_ids
    assert any(dep["title"] == "Dependency" for dep in data["dependencies"])


# ── New subcommand tests ────────────────────────────────────────────


def test_cli_update(temp_db, tmp_path, capsys):
    """Test updating an issue."""
    cli = HiveCLI(temp_db, str(tmp_path))

    issue_id = temp_db.create_issue("Original title", project=tmp_path.name)
    cli.update(issue_id, title="Updated title")

    issue = temp_db.get_issue(issue_id)
    assert issue["title"] == "Updated title"


def test_cli_update_tags_in_event_log(temp_db, tmp_path):
    """Updating tags must record 'tags' in the updated event fields list.

    Regression: the event log omitted 'tags' from the fields list even when
    tags was the only field changed, producing an uninformative {fields: []}.
    """
    cli = HiveCLI(temp_db, str(tmp_path))

    issue_id = temp_db.create_issue("Tag test issue", project=tmp_path.name)
    cli.update(issue_id, tags="python,bugfix")

    # Verify the tag was persisted
    issue = temp_db.get_issue(issue_id)
    assert json.loads(issue["tags"]) == ["bugfix", "python"]

    # Verify event log records "tags" in fields
    events = temp_db.get_events(issue_id=issue_id, event_type="updated")
    assert events, "Expected an 'updated' event"
    detail = json.loads(events[0]["detail"])
    assert "tags" in detail["fields"], f"Expected 'tags' in fields, got: {detail['fields']}"


def test_cli_list_issues_limit_is_integer(temp_db, tmp_path):
    """list_issues must respect the integer limit parameter without type coercion bugs.

    Regression: limit was passed as str(limit) to SQLite which, while accepted
    implicitly, is a type error.
    """
    cli = HiveCLI(temp_db, str(tmp_path))

    for i in range(10):
        temp_db.create_issue(f"Issue {i}", project=tmp_path.name)

    result = cli.list_issues(limit=3)
    assert result["count"] == 3


def test_cli_cancel(temp_db, tmp_path, capsys):
    """Test canceling an issue."""
    cli = HiveCLI(temp_db, str(tmp_path))

    issue_id = temp_db.create_issue("To cancel", project=tmp_path.name)
    cli.cancel(issue_id, reason="no longer needed")

    issue = temp_db.get_issue(issue_id)
    assert issue["status"] == "canceled"


def test_cli_finalize(temp_db, tmp_path, capsys):
    """Test finalizing an issue."""
    cli = HiveCLI(temp_db, str(tmp_path))

    issue_id = temp_db.create_issue("To finalize", project=tmp_path.name)
    cli.finalize(issue_id, resolution="completed manually")

    issue = temp_db.get_issue(issue_id)
    assert issue["status"] == "finalized"


def test_cli_finalize_marks_merge_queue_merged(temp_db, tmp_path):
    """Manual finalize should close queued merge entries for the issue."""
    cli = HiveCLI(temp_db, str(tmp_path))

    issue_id = temp_db.create_issue("To finalize", project=tmp_path.name)
    temp_db.try_transition_issue_status(issue_id, to_status="done")
    temp_db.conn.execute(
        """
        INSERT INTO merge_queue (issue_id, agent_id, project, worktree, branch_name, status)
        VALUES (?, ?, ?, ?, ?, 'queued')
        """,
        (issue_id, "agent-1", tmp_path.name, str(tmp_path / ".worktrees" / "agent-1"), "agent/agent-1"),
    )
    temp_db.conn.commit()

    cli.finalize(issue_id, resolution="manual review")

    row = temp_db.conn.execute("SELECT status, completed_at FROM merge_queue WHERE issue_id = ?", (issue_id,)).fetchone()
    assert row is not None
    assert row["status"] == "merged"
    assert row["completed_at"] is not None


def test_cli_review_lists_done_issues(temp_db, tmp_path, capsys):
    """Review command should show done issues with actionable git/finalize hints."""
    cli = HiveCLI(temp_db, str(tmp_path))

    issue_id = temp_db.create_issue("Ready for review", project=tmp_path.name)
    temp_db.try_transition_issue_status(issue_id, to_status="done")
    temp_db.conn.execute(
        """
        INSERT INTO merge_queue (issue_id, agent_id, project, worktree, branch_name, status)
        VALUES (?, ?, ?, ?, ?, 'queued')
        """,
        (issue_id, "agent-2", tmp_path.name, str(tmp_path / ".worktrees" / "agent-2"), "agent/agent-2"),
    )
    temp_db.conn.commit()

    cli.review()

    captured = capsys.readouterr()
    assert "Ready for review" in captured.out
    assert "git -C" in captured.out
    assert "hive finalize" in captured.out


def test_cli_review_json(temp_db, tmp_path, capsys):
    """Review command should support JSON output."""
    cli = HiveCLI(temp_db, str(tmp_path))

    issue_id = temp_db.create_issue("JSON review", project=tmp_path.name)
    temp_db.try_transition_issue_status(issue_id, to_status="done")
    temp_db.conn.execute(
        """
        INSERT INTO merge_queue (issue_id, agent_id, project, worktree, branch_name, status)
        VALUES (?, ?, ?, ?, ?, 'queued')
        """,
        (issue_id, "agent-3", tmp_path.name, str(tmp_path / ".worktrees" / "agent-3"), "agent/agent-3"),
    )
    temp_db.conn.commit()

    cli.review()

    captured = capsys.readouterr()
    data = toon.decode(captured.out)
    assert data["count"] == 1
    assert data["review"][0]["id"] == issue_id
    assert "finalize_hint" in data["review"][0]


def test_cli_retry(temp_db, tmp_path, capsys):
    """Test retrying a failed issue."""
    cli = HiveCLI(temp_db, str(tmp_path))

    issue_id = temp_db.create_issue("Escalated task", project=tmp_path.name)
    temp_db.try_transition_issue_status(issue_id, to_status="escalated")

    cli.retry(issue_id, notes="try different approach")

    issue = temp_db.get_issue(issue_id)
    assert issue["status"] == "open"
    assert issue["assignee"] is None


def test_cli_retry_logs_manual_retry_event(temp_db, tmp_path):
    """Test that manual retry logs 'manual_retry' event type, not 'retry'."""
    cli = HiveCLI(temp_db, str(tmp_path))

    # Create and escalate an issue
    issue_id = temp_db.create_issue("Escalated task", project=tmp_path.name)
    temp_db.try_transition_issue_status(issue_id, to_status="escalated")

    # Retry the issue manually
    cli.retry(issue_id, notes="manual retry test")

    # Verify the event type is 'manual_retry' and 'retry' count is 0
    manual_retry_count = temp_db.count_events(issue_id, "manual_retry")
    retry_count = temp_db.count_events(issue_id, "retry")

    assert manual_retry_count == 1, "Should have exactly 1 manual_retry event"
    assert retry_count == 0, "Should have 0 retry events (only manual_retry)"


def test_cli_retry_reset_logs_retry_reset_event(temp_db, tmp_path):
    """Test that retry --reset logs a retry_reset event."""
    cli = HiveCLI(temp_db, str(tmp_path))

    issue_id = temp_db.create_issue("Escalated task", project=tmp_path.name)
    temp_db.try_transition_issue_status(issue_id, to_status="escalated")

    result = cli.retry(issue_id, notes="fixed the root cause", reset=True)

    # Issue should be open
    issue = temp_db.get_issue(issue_id)
    assert issue["status"] == "open"
    assert result["reset"] is True

    # Should have both retry_reset and manual_retry events
    reset_count = temp_db.count_events(issue_id, "retry_reset")
    manual_retry_count = temp_db.count_events(issue_id, "manual_retry")
    assert reset_count == 1
    assert manual_retry_count == 1


def test_cli_retry_without_reset_no_retry_reset_event(temp_db, tmp_path):
    """Test that retry without --reset does not log a retry_reset event."""
    cli = HiveCLI(temp_db, str(tmp_path))

    issue_id = temp_db.create_issue("Escalated task", project=tmp_path.name)
    temp_db.try_transition_issue_status(issue_id, to_status="escalated")

    result = cli.retry(issue_id, notes="try again")

    assert result["reset"] is False
    reset_count = temp_db.count_events(issue_id, "retry_reset")
    assert reset_count == 0


def test_cli_dep_add_remove(temp_db, tmp_path, capsys):
    """Test adding and removing dependencies."""
    cli = HiveCLI(temp_db, str(tmp_path))

    issue1 = temp_db.create_issue("Blocker", project=tmp_path.name)
    issue2 = temp_db.create_issue("Blocked", project=tmp_path.name)

    cli.dep_add(issue2, issue1)

    captured = capsys.readouterr()
    assert "dependency" in captured.out.lower()

    # Verify dependency exists
    cursor = temp_db.conn.execute("SELECT * FROM dependencies WHERE issue_id = ? AND depends_on = ?", (issue2, issue1))
    assert cursor.fetchone() is not None

    cli.dep_remove(issue2, issue1)

    # Verify dependency removed
    cursor = temp_db.conn.execute("SELECT * FROM dependencies WHERE issue_id = ? AND depends_on = ?", (issue2, issue1))
    assert cursor.fetchone() is None


def test_cli_agents(temp_db, tmp_path, capsys):
    """Test listing agents."""
    cli = HiveCLI(temp_db, str(tmp_path))

    cli.list_agents()

    data = toon.decode(capsys.readouterr().out)
    assert data["count"] == 0
    assert "note" in data  # explicit empty-state message


def test_cli_logs(temp_db, tmp_path, capsys):
    """Test getting events via logs."""
    cli = HiveCLI(temp_db, str(tmp_path))

    # Create an issue to generate events
    temp_db.create_issue("Event test", project=tmp_path.name)

    cli.logs(n=5)

    captured = capsys.readouterr()
    assert "created" in captured.out


def test_cli_costs_no_data(temp_db, tmp_path, capsys):
    """Test metrics --costs with no token usage data."""
    cli = HiveCLI(temp_db, str(tmp_path))

    cli.metrics(show_costs=True)

    data = toon.decode(capsys.readouterr().out)
    assert data["total_tokens"] == 0
    assert data["estimated_cost_usd"] == 0


def test_cli_costs_with_data(temp_db, tmp_path, capsys):
    """Test metrics --costs with token usage data."""
    cli = HiveCLI(temp_db, str(tmp_path))

    # Create issue and agent
    issue_id = temp_db.create_issue("Test issue", project=tmp_path.name)
    agent_id = temp_db.create_agent("test-agent")

    # Add some token usage events
    temp_db.log_event(issue_id, agent_id, "tokens_used", {"input_tokens": 1000, "output_tokens": 500, "model": "claude-sonnet-4-5-20250929"})
    temp_db.log_event(issue_id, agent_id, "tokens_used", {"input_tokens": 2000, "output_tokens": 1000, "model": "claude-sonnet-4-5-20250929"})

    cli.metrics(show_costs=True)

    data = toon.decode(capsys.readouterr().out)
    assert data["total_tokens"] == 4500
    assert data["total_input_tokens"] == 3000
    assert data["total_output_tokens"] == 1500
    assert "estimated_cost_usd" in data


def test_cli_costs_json(temp_db, tmp_path, capsys):
    """Test metrics --costs with JSON output."""
    cli = HiveCLI(temp_db, str(tmp_path))

    # Create issue and agent with token usage
    issue_id = temp_db.create_issue("Test issue", project=tmp_path.name)
    agent_id = temp_db.create_agent("test-agent")

    temp_db.log_event(issue_id, agent_id, "tokens_used", {"input_tokens": 1000, "output_tokens": 500, "model": "claude-sonnet-4-5-20250929"})

    cli.metrics(show_costs=True)

    captured = capsys.readouterr()
    data = toon.decode(captured.out)
    assert data["total_tokens"] == 1500
    assert data["total_input_tokens"] == 1000
    assert data["total_output_tokens"] == 500
    assert "estimated_cost_usd" in data
    assert "issue_breakdown" in data
    assert "agent_breakdown" in data
    assert "model_breakdown" in data


def test_cli_costs_by_issue(temp_db, tmp_path, capsys):
    """Test metrics --costs filtered by issue."""
    cli = HiveCLI(temp_db, str(tmp_path))

    # Create issues and agents with token usage
    issue1_id = temp_db.create_issue("Issue 1", project=tmp_path.name)
    issue2_id = temp_db.create_issue("Issue 2", project=tmp_path.name)
    agent_id = temp_db.create_agent("test-agent")

    # Add token usage for both issues
    temp_db.log_event(issue1_id, agent_id, "tokens_used", {"input_tokens": 1000, "output_tokens": 500})
    temp_db.log_event(issue2_id, agent_id, "tokens_used", {"input_tokens": 2000, "output_tokens": 1000})

    # Filter by issue1
    cli.metrics(show_costs=True, issue_id=issue1_id)

    data = toon.decode(capsys.readouterr().out)
    assert data["issue_id"] == issue1_id
    assert data["total_tokens"] == 1500


def test_cli_costs_by_agent(temp_db, tmp_path, capsys):
    """Test metrics --costs filtered by agent."""
    cli = HiveCLI(temp_db, str(tmp_path))

    # Create issues and agents with token usage
    issue_id = temp_db.create_issue("Test issue", project=tmp_path.name)
    agent1_id = temp_db.create_agent("agent-1")
    agent2_id = temp_db.create_agent("agent-2")

    # Add token usage for both agents
    temp_db.log_event(issue_id, agent1_id, "tokens_used", {"input_tokens": 1000, "output_tokens": 500})
    temp_db.log_event(issue_id, agent2_id, "tokens_used", {"input_tokens": 2000, "output_tokens": 1000})

    # Filter by agent1
    cli.metrics(show_costs=True, agent_id=agent1_id)

    data = toon.decode(capsys.readouterr().out)
    assert data["agent_id"] == agent1_id
    assert data["total_tokens"] == 1500


# ── Notes CLI tests ─────────────────────────────────────────────


def test_cli_add_note(temp_db, tmp_path, capsys):
    """Test adding a note via CLI."""
    cli = HiveCLI(temp_db, str(tmp_path))

    cli.add_note("Test discovery note")

    data = toon.decode(capsys.readouterr().out)
    assert data["category"] == "discovery"
    assert "note_id" in data

    # Verify note was stored
    notes = temp_db.get_notes(limit=1)
    assert len(notes) == 1
    assert notes[0]["content"] == "Test discovery note"
    assert notes[0]["category"] == "discovery"


def test_cli_add_note_with_issue_and_category(temp_db, tmp_path, capsys):
    """Test adding a note with issue and category."""
    cli = HiveCLI(temp_db, str(tmp_path))

    issue_id = temp_db.create_issue("Test issue", project=tmp_path.name)
    cli.add_note("Watch out for X", issue_id=issue_id, category="gotcha")

    data = toon.decode(capsys.readouterr().out)
    assert data["category"] == "gotcha"

    notes = temp_db.get_notes(issue_id=issue_id)
    assert len(notes) == 1
    assert notes[0]["category"] == "gotcha"
    assert notes[0]["issue_id"] == issue_id


# ── Setup wizard tests ──────────────────────────────────────────


def test_setup_creates_config(tmp_path, capsys):
    """Test setup creates .hive.toml with claude backend."""
    from hive.cli import _do_setup

    _do_setup(tmp_path, tmp_path.name)

    captured = capsys.readouterr()
    data = toon.decode(captured.out)
    assert "config_created" in data

    config = (tmp_path / ".hive.toml").read_text()
    assert 'backend = "claude"' in config
    assert "merge_queue_enabled = false" in config
    assert tmp_path.name in config


def test_setup_skips_existing_config(tmp_path, capsys):
    """Test setup doesn't overwrite existing config."""
    from hive.cli import _do_setup

    (tmp_path / ".hive.toml").write_text('[hive]\nbackend = "claude"\n')

    _do_setup(tmp_path, tmp_path.name)

    captured = capsys.readouterr()
    data = toon.decode(captured.out)
    assert data["config_exists"] is True

    # Original content preserved
    assert "claude" in (tmp_path / ".hive.toml").read_text()


def test_queen_auto_starts_daemon(temp_db, tmp_path):
    """`hive queen` should start daemon when not running."""
    cli = HiveCLI(temp_db, str(tmp_path))

    daemon = unittest.mock.Mock()
    daemon.status.side_effect = [
        {"running": False, "pid": None},
        {"running": True, "pid": 9012},
    ]
    daemon.start.return_value = True

    with (
        unittest.mock.patch.object(cli, "_make_daemon", return_value=daemon),
        unittest.mock.patch.object(cli, "_queen_claude") as queen_claude,
    ):
        cli.queen(backend="claude")

    daemon.start.assert_called_once()
    queen_claude.assert_called_once()


def _make_running_daemon():
    """Helper: create a mock daemon that reports as already running."""
    daemon = unittest.mock.Mock()
    daemon.status.return_value = {"running": True, "pid": 1234}
    return daemon


def test_queen_headless_claude_command_flags(temp_db, tmp_path):
    """Headless queen should pass --print, -p, --dangerously-skip-permissions, and headless system prompt."""
    cli = HiveCLI(temp_db, str(tmp_path))

    with (
        unittest.mock.patch.object(cli, "_make_daemon", return_value=_make_running_daemon()),
        unittest.mock.patch("hive.cli.queen.subprocess.run", return_value=unittest.mock.Mock(returncode=0)) as mock_run,
        unittest.mock.patch.object(cli, "_queen_write_identity_files", return_value=tmp_path / "q.md"),
        unittest.mock.patch.object(cli, "_queen_cleanup_identity_files"),
    ):
        cli.queen(backend="claude", headless=True, prompt="Bump deps")

    cmd = mock_run.call_args[0][0]
    assert "--print" in cmd
    assert "-p" in cmd
    assert cmd[cmd.index("-p") + 1] == "Bump deps"
    assert "--dangerously-skip-permissions" in cmd
    # Should have three --append-system-prompt entries (short + queen identity + headless)
    asp_indices = [i for i, x in enumerate(cmd) if x == "--append-system-prompt"]
    assert len(asp_indices) == 3
    headless_prompt = cmd[asp_indices[2] + 1]
    assert "HEADLESS MODE" in headless_prompt


def test_queen_headless_codex_approval_never(temp_db, tmp_path):
    """Headless codex queen should force approval policy to 'never'."""
    cli = HiveCLI(temp_db, str(tmp_path))

    with (
        unittest.mock.patch.object(cli, "_make_daemon", return_value=_make_running_daemon()),
        unittest.mock.patch("hive.cli.queen.subprocess.run", return_value=unittest.mock.Mock(returncode=0)) as mock_run,
        unittest.mock.patch.object(cli, "_queen_write_identity_files", return_value=tmp_path / "q.md"),
        unittest.mock.patch.object(cli, "_queen_cleanup_identity_files"),
    ):
        cli.queen(backend="codex", headless=True, prompt="Add tests")

    cmd = mock_run.call_args[0][0]
    # approval should be "never"
    approval_idx = cmd.index("--ask-for-approval")
    assert cmd[approval_idx + 1] == "never"
    # prompt should contain the task
    assert any("Add tests" in arg for arg in cmd)


def test_queen_headless_does_not_sys_exit(temp_db, tmp_path):
    """Headless queen should return normally instead of calling sys.exit."""
    cli = HiveCLI(temp_db, str(tmp_path))

    with (
        unittest.mock.patch.object(cli, "_make_daemon", return_value=_make_running_daemon()),
        unittest.mock.patch("hive.cli.queen.subprocess.run", return_value=unittest.mock.Mock(returncode=0)),
        unittest.mock.patch.object(cli, "_queen_write_identity_files", return_value=tmp_path / "q.md"),
        unittest.mock.patch.object(cli, "_queen_cleanup_identity_files"),
    ):
        # Should NOT raise SystemExit
        cli.queen(backend="claude", headless=True, prompt="Do something")


def test_queen_headless_nonzero_exit_raises(temp_db, tmp_path):
    """Headless queen with nonzero exit code should raise an error."""
    cli = HiveCLI(temp_db, str(tmp_path))

    with (
        unittest.mock.patch.object(cli, "_make_daemon", return_value=_make_running_daemon()),
        unittest.mock.patch("hive.cli.queen.subprocess.run", return_value=unittest.mock.Mock(returncode=1)),
        unittest.mock.patch.object(cli, "_queen_write_identity_files", return_value=tmp_path / "q.md"),
        unittest.mock.patch.object(cli, "_queen_cleanup_identity_files"),
        pytest.raises(SystemExit),
    ):
        cli.queen(backend="claude", headless=True, prompt="Fail task")


def test_queen_headless_sets_skip_permissions_env(temp_db, tmp_path):
    """Headless mode should set HIVE_CLAUDE_SKIP_PERMISSIONS=1 for worker propagation."""
    cli = HiveCLI(temp_db, str(tmp_path))

    with (
        unittest.mock.patch.object(cli, "_make_daemon", return_value=_make_running_daemon()),
        unittest.mock.patch("hive.cli.queen.subprocess.run", return_value=unittest.mock.Mock(returncode=0)),
        unittest.mock.patch.object(cli, "_queen_write_identity_files", return_value=tmp_path / "q.md"),
        unittest.mock.patch.object(cli, "_queen_cleanup_identity_files"),
        unittest.mock.patch.dict("os.environ", {}, clear=False),
    ):
        cli.queen(backend="claude", headless=True, prompt="Task")
        import os

        assert os.environ.get("HIVE_CLAUDE_SKIP_PERMISSIONS") == "1"


def test_queen_interactive_unchanged(temp_db, tmp_path):
    """Non-headless queen should still call _queen_claude without headless flags."""
    cli = HiveCLI(temp_db, str(tmp_path))

    with (
        unittest.mock.patch.object(cli, "_make_daemon", return_value=_make_running_daemon()),
        unittest.mock.patch.object(cli, "_queen_claude") as mock_claude,
    ):
        cli.queen(backend="claude")

    mock_claude.assert_called_once_with(skip_permissions=False, mcp_configs=[], headless=False, prompt=None)


def test_queen_mode_option_removed(capsys):
    """Queen no longer accepts mode-specific prompt flags."""
    with pytest.raises(SystemExit) as exc_info:
        run_typer(["queen", "--mode", "competitive"])

    assert exc_info.value.code != 0
    data = toon.decode(capsys.readouterr().out)
    assert "No such option" in data["error"]


def test_create_with_parent_and_metadata(temp_db, tmp_path):
    """Create issue with --parent and --metadata."""
    cli = HiveCLI(temp_db, str(tmp_path))
    epic = cli.create("Epic", issue_type="epic")
    epic_id = epic["id"]

    result = cli.create("Child", parent_id=epic_id, metadata='{"source":"test"}')
    assert result["parent_id"] == epic_id

    issue = temp_db.get_issue(result["id"])
    assert issue["parent_id"] == epic_id
    stored = json.loads(issue["metadata"])
    assert stored["source"] == "test"


def test_list_empty_suggests_create(temp_db, tmp_path, capsys):
    """Test that empty list emits a note suggesting hive create."""
    cli = HiveCLI(temp_db, str(tmp_path))
    cli.list_issues()

    data = toon.decode(capsys.readouterr().out)
    assert data["count"] == 0
    assert "hive create" in data["note"]


def test_status_no_issues_suggests_create(temp_db, tmp_path, capsys):
    """Test that status with 0 issues emits a note suggesting hive create."""
    cli = HiveCLI(temp_db, str(tmp_path))
    cli.status()

    data = toon.decode(capsys.readouterr().out)
    assert data["total_issues"] == 0
    assert "hive create" in data["note"]


def test_status_with_issues_no_hint(temp_db, tmp_path, capsys):
    """Test that status with issues does NOT emit the empty-state note."""
    cli = HiveCLI(temp_db, str(tmp_path))
    temp_db.create_issue("Existing issue", project=tmp_path.name)
    cli.status()

    data = toon.decode(capsys.readouterr().out)
    assert "note" not in data


def test_status_includes_daemon_info_json(temp_db, tmp_path, capsys):
    """Test that status --json includes daemon info."""
    cli = HiveCLI(temp_db, str(tmp_path))
    cli.status()

    captured = capsys.readouterr()
    result = toon.decode(captured.out)
    assert "daemon" in result
    assert "running" in result["daemon"]
    assert "pid" in result["daemon"]
    assert "log_file" in result["daemon"]


def test_status_shows_daemon_info(temp_db, tmp_path, capsys):
    """Test that status includes a daemon object."""
    cli = HiveCLI(temp_db, str(tmp_path))
    cli.status()

    data = toon.decode(capsys.readouterr().out)
    assert "daemon" in data
    assert "running" in data["daemon"]


# ── Status worker/refinery detail tests ────────────────────────────────


def test_status_shows_worker_details(temp_db, tmp_path, capsys):
    """Status should list each active worker with its issue."""
    cli = HiveCLI(temp_db, str(tmp_path))

    # Create issues
    issue_id = temp_db.create_issue("Add auth module", project=tmp_path.name)
    temp_db.try_transition_issue_status(issue_id, to_status="in_progress")

    # Create a working agent
    agent_id = temp_db.create_agent(name="worker-abc123", project=tmp_path.name)
    temp_db.conn.execute(
        "UPDATE agents SET status = 'working', current_issue = ? WHERE id = ?",
        (issue_id, agent_id),
    )
    temp_db.conn.commit()

    cli.status()
    captured = capsys.readouterr()
    assert "worker-abc123" in captured.out
    assert issue_id in captured.out
    assert "Add auth module" in captured.out


def test_status_shows_refinery_reviewing(temp_db, tmp_path, capsys):
    """Status should show refinery reviewing when a merge is running."""
    cli = HiveCLI(temp_db, str(tmp_path))

    issue_id = temp_db.create_issue("Fix login bug", project=tmp_path.name)
    temp_db.try_transition_issue_status(issue_id, to_status="done")

    # Insert a running merge entry
    temp_db.conn.execute(
        """
        INSERT INTO merge_queue (issue_id, agent_id, project, worktree, branch_name, status)
        VALUES (?, ?, ?, ?, ?, 'running')
        """,
        (issue_id, "agent-1", tmp_path.name, "/tmp/wt", "agent/agent-1"),
    )
    temp_db.conn.commit()

    cli.status()
    data = toon.decode(capsys.readouterr().out)
    assert data["refinery"]["active"] is True
    assert data["refinery"]["issue_id"] == issue_id
    assert data["refinery"]["issue_title"] == "Fix login bug"


def test_status_shows_refinery_idle(temp_db, tmp_path, capsys):
    """Status should show refinery inactive when no merge is running."""
    cli = HiveCLI(temp_db, str(tmp_path))
    cli.status()

    data = toon.decode(capsys.readouterr().out)
    assert data["refinery"]["active"] is False


def test_status_json_includes_workers_and_refinery(temp_db, tmp_path, capsys):
    """JSON status should include workers list and refinery object."""
    cli = HiveCLI(temp_db, str(tmp_path))

    issue_id = temp_db.create_issue("Task A", project=tmp_path.name)
    temp_db.try_transition_issue_status(issue_id, to_status="in_progress")

    agent_id = temp_db.create_agent(name="worker-xyz", project=tmp_path.name)
    temp_db.conn.execute(
        "UPDATE agents SET status = 'working', current_issue = ? WHERE id = ?",
        (issue_id, agent_id),
    )
    temp_db.conn.commit()

    cli.status()
    captured = capsys.readouterr()
    data = toon.decode(captured.out)

    assert "workers" in data
    assert len(data["workers"]) == 1
    assert data["workers"][0]["name"] == "worker-xyz"
    assert data["workers"][0]["issue_id"] == issue_id
    assert data["workers"][0]["issue_title"] == "Task A"

    assert "refinery" in data
    assert data["refinery"]["active"] is False


def test_status_json_refinery_active(temp_db, tmp_path, capsys):
    """JSON status should show active refinery when merge is running."""
    cli = HiveCLI(temp_db, str(tmp_path))

    issue_id = temp_db.create_issue("Running merge", project=tmp_path.name)
    temp_db.conn.execute(
        """
        INSERT INTO merge_queue (issue_id, agent_id, project, worktree, branch_name, status)
        VALUES (?, ?, ?, ?, ?, 'running')
        """,
        (issue_id, "agent-1", tmp_path.name, "/tmp/wt", "agent/agent-1"),
    )
    temp_db.conn.commit()

    cli.status()
    captured = capsys.readouterr()
    data = toon.decode(captured.out)

    assert data["refinery"]["active"] is True
    assert data["refinery"]["issue_id"] == issue_id
    assert data["refinery"]["issue_title"] == "Running merge"


# ── Merges summary footer tests ──────────────────────────────────────


def _insert_merge_entry(db, issue_id, agent_id, project, status):
    """Helper: insert a merge_queue row with the given status."""
    db.conn.execute(
        """
        INSERT INTO merge_queue (issue_id, agent_id, project, worktree, branch_name, status)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (issue_id, agent_id, project, f"/worktrees/{agent_id}", f"agent/{agent_id}", status),
    )
    db.conn.commit()


def test_merges_summary_footer_shows_counts_by_status(temp_db, tmp_path, capsys):
    """merges output should show a summary footer with counts per status, not just a total."""
    cli = HiveCLI(temp_db, str(tmp_path))

    issue1 = temp_db.create_issue("Issue 1", project=tmp_path.name)
    issue2 = temp_db.create_issue("Issue 2", project=tmp_path.name)
    issue3 = temp_db.create_issue("Issue 3", project=tmp_path.name)
    issue4 = temp_db.create_issue("Issue 4", project=tmp_path.name)

    _insert_merge_entry(temp_db, issue1, "a1", tmp_path.name, "queued")
    _insert_merge_entry(temp_db, issue2, "a2", tmp_path.name, "running")
    _insert_merge_entry(temp_db, issue3, "a3", tmp_path.name, "merged")
    _insert_merge_entry(temp_db, issue4, "a4", tmp_path.name, "failed")

    cli.merges()

    data = toon.decode(capsys.readouterr().out)
    assert data["status_counts"] == {"queued": 1, "running": 1, "merged": 1, "failed": 1}


def test_merges_summary_footer_omits_zero_statuses(temp_db, tmp_path, capsys):
    """merges footer should not mention statuses with zero entries."""
    cli = HiveCLI(temp_db, str(tmp_path))

    issue1 = temp_db.create_issue("Only merged", project=tmp_path.name)
    _insert_merge_entry(temp_db, issue1, "a1", tmp_path.name, "merged")

    cli.merges()

    data = toon.decode(capsys.readouterr().out)
    # Only the populated status appears; zero-count statuses are omitted.
    assert data["status_counts"] == {"merged": 1}


def test_merges_summary_aggregates_multiple_same_status(temp_db, tmp_path, capsys):
    """merges footer should sum entries of the same status correctly."""
    cli = HiveCLI(temp_db, str(tmp_path))

    for i in range(3):
        issue_id = temp_db.create_issue(f"Merged issue {i}", project=tmp_path.name)
        _insert_merge_entry(temp_db, issue_id, f"a{i}", tmp_path.name, "merged")

    issue_queued = temp_db.create_issue("Queued issue", project=tmp_path.name)
    _insert_merge_entry(temp_db, issue_queued, "aq", tmp_path.name, "queued")

    cli.merges()

    data = toon.decode(capsys.readouterr().out)
    assert data["status_counts"]["merged"] == 3
    assert data["status_counts"]["queued"] == 1


def test_merges_json_includes_status_counts(temp_db, tmp_path, capsys):
    """merges --json output should include a status_counts field."""
    cli = HiveCLI(temp_db, str(tmp_path))

    issue1 = temp_db.create_issue("Q1", project=tmp_path.name)
    issue2 = temp_db.create_issue("Q2", project=tmp_path.name)
    issue3 = temp_db.create_issue("M1", project=tmp_path.name)

    _insert_merge_entry(temp_db, issue1, "a1", tmp_path.name, "queued")
    _insert_merge_entry(temp_db, issue2, "a2", tmp_path.name, "queued")
    _insert_merge_entry(temp_db, issue3, "a3", tmp_path.name, "merged")

    cli.merges()

    captured = capsys.readouterr()
    data = toon.decode(captured.out)
    assert "status_counts" in data
    assert data["status_counts"]["queued"] == 2
    assert data["status_counts"]["merged"] == 1
    assert data["count"] == 3


def test_merges_empty_shows_no_entries_message(temp_db, tmp_path, capsys):
    """merges with no entries should emit count 0 and an explicit empty-state note."""
    cli = HiveCLI(temp_db, str(tmp_path))

    cli.merges()

    data = toon.decode(capsys.readouterr().out)
    assert data["count"] == 0
    assert "note" in data
    assert not data["merges"]


def test_cli_note_without_targets_creates_project_note(temp_db, tmp_path, capsys):
    """hive note without targets creates a normal project note."""
    cli = HiveCLI(temp_db, str(tmp_path))

    cli.add_note("Project note")

    data = toon.decode(capsys.readouterr().out)
    assert data["category"] == "discovery"
    assert "note_id" in data

    notes = temp_db.get_notes(limit=1)
    assert notes[0]["content"] == "Project note"
    assert notes[0]["project"] == tmp_path.name


# ── cli_command decorator invariant tests ────────────────────────────────────


def test_cli_command_inv1_machine_mode_produces_valid_toon(temp_db, tmp_path, capsys):
    """INV-1: Every decorated command in machine mode produces valid TOON to stdout on success."""
    cli = HiveCLI(temp_db, str(tmp_path))

    # Spot-check: create, list_issues, cancel
    cli.create("INV1 issue")
    out = capsys.readouterr().out
    data = toon.decode(out)
    assert "id" in data

    cli.list_issues()
    out = capsys.readouterr().out
    data = toon.decode(out)
    assert "issues" in data

    issue_id = temp_db.create_issue("To cancel", project=tmp_path.name)
    cli.cancel(issue_id, reason="test")
    out = capsys.readouterr().out
    data = toon.decode(out)
    assert data["issue_id"] == issue_id


def test_cli_command_inv2_exception_produces_error_toon(temp_db, tmp_path, capsys):
    """INV-2: Every decorated command in machine mode produces `error: <msg>` TOON on exception."""
    cli = HiveCLI(temp_db, str(tmp_path))

    with pytest.raises(SystemExit):
        cli.show("nonexistent-id-xyz")

    out = capsys.readouterr().out
    data = toon.decode(out)
    assert "error" in data
    assert isinstance(data["error"], str)
    assert len(data["error"]) > 0


def test_cli_command_inv2_exception_exits_nonzero(temp_db, tmp_path):
    """INV-2b: Decorated command exits with code 1 on exception."""
    cli = HiveCLI(temp_db, str(tmp_path))

    with pytest.raises(SystemExit) as exc_info:
        cli.show("nonexistent-id-xyz")
    assert exc_info.value.code == 1


def test_cli_invoke_raw_returns_result_without_printing(temp_db, tmp_path, capsys):
    """Raw invocation should return data without emitting output."""
    cli = HiveCLI(temp_db, str(tmp_path))

    result = cli.invoke_raw("create", "Raw title", "Raw description", priority=3)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert result["title"] == "Raw title"
    assert result["priority"] == 3


def test_cli_command_emits_toon_with_help(temp_db, tmp_path, capsys):
    """run_command encodes the result as TOON with a trailing help[] block."""
    cli = HiveCLI(temp_db, str(tmp_path))

    result = cli.run_command("create", "Formatted title")

    out = capsys.readouterr().out
    data = toon.decode(out)
    assert data["title"] == "Formatted title"
    assert result["title"] == "Formatted title"
    assert "help[" in out  # next-step hints are appended in TOON mode


# ── Cleanup ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def git_project(tmp_path):
    """Create a minimal git repo suitable for cleanup tests."""
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=str(tmp_path), capture_output=True, check=True)
    return tmp_path


def test_cleanup_removes_hive_dir(temp_db, git_project, capsys):
    """cleanup should remove the .hive/ directory."""
    hive_dir = git_project / ".hive"
    hive_dir.mkdir()
    (hive_dir / "project-context.md").write_text("ctx")
    (hive_dir / "queen-context.md").write_text("queen")

    cli = HiveCLI(temp_db, str(git_project))
    cli.cleanup()

    captured = capsys.readouterr()
    data = toon.decode(captured.out)
    assert not hive_dir.exists()
    assert any(".hive" in r for r in data["removed"])
    assert data["dry_run"] is False


def test_cleanup_removes_hive_toml(temp_db, git_project, capsys):
    """cleanup should remove .hive.toml."""
    toml = git_project / ".hive.toml"
    toml.write_text('[hive]\nbackend = "claude"\n')

    cli = HiveCLI(temp_db, str(git_project))
    cli.cleanup()

    captured = capsys.readouterr()
    data = toon.decode(captured.out)
    assert not toml.exists()
    assert any(".hive.toml" in r for r in data["removed"])


def test_cleanup_removes_worktrees(temp_db, git_project, capsys):
    """cleanup should remove .worktrees/ and prune git metadata."""
    worktrees = git_project / ".worktrees"
    worktrees.mkdir()
    (worktrees / "worker-abc").mkdir()
    (worktrees / "worker-abc" / "file.txt").write_text("wip")

    cli = HiveCLI(temp_db, str(git_project))
    cli.cleanup()

    captured = capsys.readouterr()
    data = toon.decode(captured.out)
    assert not worktrees.exists()
    assert any(".worktrees" in r for r in data["removed"])


def test_cleanup_removes_agent_branches(temp_db, git_project, capsys):
    """cleanup should delete agent/* branches."""
    subprocess.run(["git", "branch", "agent/worker-1"], cwd=str(git_project), capture_output=True, check=True)
    subprocess.run(["git", "branch", "agent/worker-2"], cwd=str(git_project), capture_output=True, check=True)

    cli = HiveCLI(temp_db, str(git_project))
    cli.cleanup()

    captured = capsys.readouterr()
    data = toon.decode(captured.out)
    assert len([r for r in data["removed"] if r.startswith("branch:")]) == 2

    result = subprocess.run(["git", "branch", "--list", "agent/*"], cwd=str(git_project), capture_output=True, text=True)
    assert result.stdout.strip() == ""


def test_cleanup_dry_run(temp_db, git_project, capsys):
    """--dry-run should report items without deleting them."""
    (git_project / ".hive.toml").write_text("x")
    hive_dir = git_project / ".hive"
    hive_dir.mkdir()

    cli = HiveCLI(temp_db, str(git_project))
    cli.cleanup(dry_run=True)

    captured = capsys.readouterr()
    data = toon.decode(captured.out)
    assert data["dry_run"] is True
    assert len(data["would_remove"]) >= 2
    assert data["removed"] == []
    # Files still exist
    assert (git_project / ".hive.toml").exists()
    assert hive_dir.exists()


def test_cleanup_nothing_to_do(temp_db, git_project, capsys):
    """cleanup on a clean project should report nothing removed."""
    cli = HiveCLI(temp_db, str(git_project))
    cli.cleanup()

    captured = capsys.readouterr()
    data = toon.decode(captured.out)
    assert data["removed"] == []
