"""Tests for refinery_first and manual merge policy routing."""

import json
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from hive.db import Database
from hive.git import create_worktree
from hive.merge import MergeProcessor


@pytest.fixture
def temp_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    db = Database(db_path)
    db.connect()
    yield db
    db.close()


@pytest.fixture
def git_repo(tmp_path):
    repo_path = tmp_path / "test_repo"
    repo_path.mkdir()
    subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_path, check=True, capture_output=True)
    (repo_path / "README.md").write_text("# Test Repo\n")
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(["git", "branch", "-M", "main"], cwd=repo_path, check=True, capture_output=True)
    return repo_path


@pytest.fixture
def mock_opencode():
    client = AsyncMock(
        spec=[
            "create_session",
            "send_message_async",
            "get_session_status",
            "get_messages",
            "abort_session",
            "cleanup_session",
        ]
    )
    return client


@pytest.fixture
def merge_entry_with_worktree(git_repo, temp_db):
    agent_id = temp_db.create_agent(name="worker-test")
    issue_id = temp_db.create_issue(title="Test Feature", project="test")
    temp_db.update_issue_status(issue_id, "done")

    worktree_path = create_worktree(str(git_repo), "worker-test")
    (Path(worktree_path) / "feature.py").write_text("# new feature\n")
    subprocess.run(["git", "add", "."], cwd=worktree_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Add feature"], cwd=worktree_path, check=True, capture_output=True)

    branch_name = "agent/worker-test"
    temp_db.conn.execute(
        "INSERT INTO merge_queue (issue_id, agent_id, project, worktree, branch_name) VALUES (?, ?, ?, ?, ?)",
        (issue_id, agent_id, "test", worktree_path, branch_name),
    )
    temp_db.conn.commit()

    return {
        "git_repo": git_repo,
        "worktree_path": worktree_path,
        "issue_id": issue_id,
        "agent_id": agent_id,
        "branch_name": branch_name,
    }


def _configure_refinery_success(mock_opencode):
    mock_opencode.create_session = AsyncMock(return_value={"id": "refinery-1"})
    mock_opencode.send_message_async = AsyncMock()
    mock_opencode.get_session_status = AsyncMock(side_effect=[{"type": "busy"}, {"type": "idle"}])
    mock_opencode.get_messages = AsyncMock(return_value=[{"parts": [{"type": "text", "text": "done"}]}])
    mock_opencode.cleanup_session = AsyncMock()


# ---------------------------------------------------------------------------
# INV-1: refinery_first never lands via mechanical authority path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refinery_first_merge_finalizes_and_merges(merge_entry_with_worktree, temp_db, mock_opencode):
    """INV-1: refinery_first merged path finalizes and lands on main (no mechanical path)."""
    info = merge_entry_with_worktree
    _configure_refinery_success(mock_opencode)

    mp = MergeProcessor(temp_db, mock_opencode, str(info["git_repo"]), "test")

    with (
        patch.object(mp, "_try_mechanical_merge", new_callable=AsyncMock) as mock_mech,
        patch("hive.merge.merge_to_main_async", new_callable=AsyncMock) as mock_merge,
        patch("hive.merge.asyncio.sleep", new_callable=AsyncMock),
        patch("hive.merge.Config") as mock_config,
        patch("hive.merge.read_result_file", return_value={"status": "merged", "summary": "ok", "conflicts_resolved": 1}),
        patch("hive.merge.remove_result_file"),
    ):
        mock_config.TEST_COMMAND = None
        mock_config.REFINERY_MODEL = "test-model"
        mock_config.LEASE_DURATION = 30
        mock_config.REFINERY_TOKEN_THRESHOLD = 100000
        mock_config.MERGE_POLICY = "refinery_first"

        await mp.process_queue_once()

    mock_mech.assert_not_called()
    mock_merge.assert_awaited_once()

    issue = temp_db.get_issue(info["issue_id"])
    assert issue["status"] == "finalized"

    row = temp_db.conn.execute("SELECT status FROM merge_queue WHERE id = 1").fetchone()
    assert row["status"] == "merged"


@pytest.mark.asyncio
async def test_refinery_first_rejected_sets_issue_open_and_queue_failed(merge_entry_with_worktree, temp_db, mock_opencode):
    """INV-2: refinery_first rejected → issue open, queue failed (no mechanical path)."""
    info = merge_entry_with_worktree
    _configure_refinery_success(mock_opencode)

    mp = MergeProcessor(temp_db, mock_opencode, str(info["git_repo"]), "test")

    with (
        patch.object(mp, "_try_mechanical_merge", new_callable=AsyncMock) as mock_mech,
        patch("hive.merge.asyncio.sleep", new_callable=AsyncMock),
        patch("hive.merge.Config") as mock_config,
        patch("hive.merge.read_result_file", return_value={"status": "rejected", "summary": "bad"}),
        patch("hive.merge.remove_result_file"),
    ):
        mock_config.TEST_COMMAND = None
        mock_config.REFINERY_MODEL = "test-model"
        mock_config.LEASE_DURATION = 30
        mock_config.REFINERY_TOKEN_THRESHOLD = 100000
        mock_config.MERGE_POLICY = "refinery_first"

        await mp.process_queue_once()

    mock_mech.assert_not_called()

    issue = temp_db.get_issue(info["issue_id"])
    assert issue["status"] == "open"

    row = temp_db.conn.execute("SELECT status FROM merge_queue WHERE id = 1").fetchone()
    assert row["status"] == "failed"


@pytest.mark.asyncio
async def test_refinery_first_needs_human_escalates_and_fails_queue(merge_entry_with_worktree, temp_db, mock_opencode):
    """INV-2: refinery_first needs_human → issue escalated, queue failed."""
    info = merge_entry_with_worktree
    _configure_refinery_success(mock_opencode)

    mp = MergeProcessor(temp_db, mock_opencode, str(info["git_repo"]), "test")

    with (
        patch.object(mp, "_try_mechanical_merge", new_callable=AsyncMock) as mock_mech,
        patch("hive.merge.asyncio.sleep", new_callable=AsyncMock),
        patch("hive.merge.Config") as mock_config,
        patch("hive.merge.read_result_file", return_value={"status": "needs_human", "summary": "unclear"}),
        patch("hive.merge.remove_result_file"),
    ):
        mock_config.TEST_COMMAND = None
        mock_config.REFINERY_MODEL = "test-model"
        mock_config.LEASE_DURATION = 30
        mock_config.REFINERY_TOKEN_THRESHOLD = 100000
        mock_config.MERGE_POLICY = "refinery_first"

        await mp.process_queue_once()

    mock_mech.assert_not_called()

    issue = temp_db.get_issue(info["issue_id"])
    assert issue["status"] == "escalated"

    row = temp_db.conn.execute("SELECT status FROM merge_queue WHERE id = 1").fetchone()
    assert row["status"] == "failed"


# ---------------------------------------------------------------------------
# Failure modes: timeout/missing result file; refinery API/session exception
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refinery_missing_result_file_escalates_and_fails(merge_entry_with_worktree, temp_db, mock_opencode):
    """Failure: missing result file → needs_human → issue escalated + queue failed."""
    info = merge_entry_with_worktree
    _configure_refinery_success(mock_opencode)

    mp = MergeProcessor(temp_db, mock_opencode, str(info["git_repo"]), "test")

    with (
        patch.object(mp, "_try_mechanical_merge", new_callable=AsyncMock) as mock_mech,
        patch("hive.merge.asyncio.sleep", new_callable=AsyncMock),
        patch("hive.merge.Config") as mock_config,
        patch("hive.merge.read_result_file", return_value=None),
        patch("hive.merge.remove_result_file"),
    ):
        mock_config.TEST_COMMAND = None
        mock_config.REFINERY_MODEL = "test-model"
        mock_config.LEASE_DURATION = 30
        mock_config.REFINERY_TOKEN_THRESHOLD = 100000
        mock_config.MERGE_POLICY = "refinery_first"

        await mp.process_queue_once()

    mock_mech.assert_not_called()

    issue = temp_db.get_issue(info["issue_id"])
    assert issue["status"] == "escalated"

    row = temp_db.conn.execute("SELECT status FROM merge_queue WHERE id = 1").fetchone()
    assert row["status"] == "failed"

    events = temp_db.get_events(issue_id=info["issue_id"], event_type="merge_escalated")
    assert len(events) == 1
    detail = json.loads(events[0]["detail"])
    assert "Refinery did not write result file" in detail["summary"]


@pytest.mark.asyncio
async def test_refinery_exception_escalates_and_fails_queue(merge_entry_with_worktree, temp_db, mock_opencode):
    """INV-4: unexpected refinery call failure escalates issue and fails queue."""
    info = merge_entry_with_worktree

    mock_opencode.create_session = AsyncMock(side_effect=RuntimeError("refinery boom"))

    mp = MergeProcessor(temp_db, mock_opencode, str(info["git_repo"]), "test")

    with (
        patch.object(mp, "_try_mechanical_merge", new_callable=AsyncMock) as mock_mech,
        patch("hive.merge.get_worktree_dirty_status_async", new_callable=AsyncMock, return_value=(False, "")),
        patch("hive.merge.Config.MERGE_POLICY", "refinery_first"),
        patch.object(mp, "_force_reset_refinery_session", new_callable=AsyncMock) as mock_reset,
    ):
        await mp.process_queue_once()

    mock_mech.assert_not_called()
    mock_reset.assert_awaited()

    issue = temp_db.get_issue(info["issue_id"])
    assert issue["status"] == "escalated"

    row = temp_db.conn.execute("SELECT status FROM merge_queue WHERE id = 1").fetchone()
    assert row["status"] == "failed"

    events = temp_db.get_events(issue_id=info["issue_id"], event_type="refinery_error")
    assert len(events) == 1


# ---------------------------------------------------------------------------
# INV-3: manual policy does not mutate queued items, logs deduped pause event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_policy_pauses_processing_and_dedupes_event(merge_entry_with_worktree, temp_db, mock_opencode):
    """INV-3: manual policy leaves queue untouched and logs pause event once."""
    info = merge_entry_with_worktree
    mp = MergeProcessor(temp_db, mock_opencode, str(info["git_repo"]), "test")

    with (
        patch.object(mp, "_mechanical_preflight", new_callable=AsyncMock) as mock_preflight,
        patch("hive.merge.Config.MERGE_POLICY", "manual"),
    ):
        await mp.process_queue_once()
        await mp.process_queue_once()

    mock_preflight.assert_not_called()

    row = temp_db.conn.execute("SELECT status FROM merge_queue WHERE id = 1").fetchone()
    assert row["status"] == "queued"

    events = temp_db.get_events(event_type="merge_paused_manual")
    assert len(events) == 1
