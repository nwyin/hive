"""Tests for CLI interface."""

import io
import sys

import pytest

from hive.cli import HiveCLI


def test_cli_create(temp_db, tmp_path):
    """Test creating an issue via CLI."""
    cli = HiveCLI(temp_db, str(tmp_path))

    issue_id = cli.create("Test issue", "Test description", priority=1)

    assert issue_id.startswith("w-")

    # Verify issue was created
    issue = temp_db.get_issue(issue_id)
    assert issue is not None
    assert issue["title"] == "Test issue"
    assert issue["description"] == "Test description"
    assert issue["priority"] == 1


def test_cli_list_issues(temp_db, tmp_path, capsys):
    """Test listing issues via CLI."""
    cli = HiveCLI(temp_db, str(tmp_path))

    # Create some issues
    temp_db.create_issue("Issue 1", priority=1, project=tmp_path.name)
    temp_db.create_issue("Issue 2", priority=2, project=tmp_path.name)
    temp_db.create_issue("Issue 3", priority=3, project=tmp_path.name)

    cli.list_issues()

    captured = capsys.readouterr()
    assert "Issue 1" in captured.out
    assert "Issue 2" in captured.out
    assert "Issue 3" in captured.out
    assert "Total: 3 issues" in captured.out


def test_cli_list_issues_by_status(temp_db, tmp_path, capsys):
    """Test listing issues filtered by status."""
    cli = HiveCLI(temp_db, str(tmp_path))

    # Create issues with different statuses
    issue1 = temp_db.create_issue("Open issue", project=tmp_path.name)
    issue2 = temp_db.create_issue("Done issue", project=tmp_path.name)
    temp_db.update_issue_status(issue2, "done")

    cli.list_issues(status="open")

    captured = capsys.readouterr()
    assert "Open issue" in captured.out
    assert "Done issue" not in captured.out


def test_cli_show_ready(temp_db, tmp_path, capsys):
    """Test showing ready queue."""
    cli = HiveCLI(temp_db, str(tmp_path))

    # Create ready issues
    temp_db.create_issue("Ready 1", priority=1, project=tmp_path.name)
    temp_db.create_issue("Ready 2", priority=2, project=tmp_path.name)

    cli.show_ready()

    captured = capsys.readouterr()
    assert "Ready 1" in captured.out
    assert "Ready 2" in captured.out
    assert "Total: 2 ready issues" in captured.out


def test_cli_show_issue(temp_db, tmp_path, capsys):
    """Test showing issue details."""
    cli = HiveCLI(temp_db, str(tmp_path))

    issue_id = temp_db.create_issue(
        "Test issue", "Detailed description", priority=1, project=tmp_path.name
    )

    cli.show(issue_id)

    captured = capsys.readouterr()
    assert issue_id in captured.out
    assert "Test issue" in captured.out
    assert "Detailed description" in captured.out
    assert "Priority: 1" in captured.out


def test_cli_close_issue(temp_db, tmp_path):
    """Test closing an issue."""
    cli = HiveCLI(temp_db, str(tmp_path))

    issue_id = temp_db.create_issue("Test issue", project=tmp_path.name)

    cli.close(issue_id)

    # Verify issue was closed
    issue = temp_db.get_issue(issue_id)
    assert issue["status"] == "canceled"


def test_cli_status(temp_db, tmp_path, capsys):
    """Test showing status."""
    cli = HiveCLI(temp_db, str(tmp_path))

    # Create some issues
    temp_db.create_issue("Open 1", project=tmp_path.name)
    temp_db.create_issue("Open 2", project=tmp_path.name)
    issue3 = temp_db.create_issue("Done 1", project=tmp_path.name)
    temp_db.update_issue_status(issue3, "done")

    cli.status()

    captured = capsys.readouterr()
    assert "Hive Status" in captured.out
    assert "open: 2" in captured.out
    assert "done: 1" in captured.out
    assert "Ready queue:" in captured.out


def test_cli_show_issue_with_dependencies(temp_db, tmp_path, capsys):
    """Test showing issue with dependencies."""
    cli = HiveCLI(temp_db, str(tmp_path))

    # Create issues with dependencies
    issue1 = temp_db.create_issue("Dependency", project=tmp_path.name)
    issue2 = temp_db.create_issue("Main task", project=tmp_path.name)

    temp_db.add_dependency(issue2, issue1)

    cli.show(issue2)

    captured = capsys.readouterr()
    assert "Depends on:" in captured.out
    assert issue1 in captured.out
    assert "Dependency" in captured.out


def test_evaluate_permission_policy():
    """Test permission policy evaluation."""
    from hive.opencode import OpenCodeClient
    from hive.orchestrator import Orchestrator

    # Create a minimal orchestrator for testing
    db = None
    opencode = OpenCodeClient()
    orch = Orchestrator(db, opencode, "/tmp", "test")

    # Test deny rules
    assert (
        orch.evaluate_permission_policy({"permission": "question", "patterns": []})
        == "reject"
    )
    assert (
        orch.evaluate_permission_policy({"permission": "plan_enter", "patterns": []})
        == "reject"
    )
    assert (
        orch.evaluate_permission_policy(
            {"permission": "external_directory", "patterns": []}
        )
        == "reject"
    )

    # Test allow rules
    assert (
        orch.evaluate_permission_policy({"permission": "read", "patterns": []})
        == "once"
    )
    assert (
        orch.evaluate_permission_policy({"permission": "edit", "patterns": []})
        == "once"
    )
    assert (
        orch.evaluate_permission_policy({"permission": "write", "patterns": []})
        == "once"
    )
    assert (
        orch.evaluate_permission_policy({"permission": "bash", "patterns": []})
        == "once"
    )

    # Test unknown permission
    assert (
        orch.evaluate_permission_policy({"permission": "unknown", "patterns": []})
        is None
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_permission_unblocker_auto_resolve(temp_db, tmp_path):
    """Test that permission unblocker auto-resolves permissions (requires OpenCode server)."""
    from hive.opencode import OpenCodeClient
    from hive.orchestrator import Orchestrator

    async with OpenCodeClient() as opencode:
        orch = Orchestrator(
            db=temp_db,
            opencode_client=opencode,
            project_path=str(tmp_path),
            project_name="test",
        )

        # Get pending permissions (should be empty initially)
        pending = await opencode.get_pending_permissions()

        # For now, just verify the method works
        # In a real scenario, we'd create a session that triggers a permission request
        # and verify it gets auto-resolved
        assert isinstance(pending, list)
