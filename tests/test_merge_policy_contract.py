"""Contract tests for merge policy names and routing."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from hive.config import _Config, parse_merge_policy
from hive.merge import MergeProcessor


@pytest.fixture
def mock_opencode():
    return AsyncMock(spec=["create_session", "send_message_async", "get_session_status", "get_messages", "cleanup_session"])


def _insert_queued_entry(temp_db, issue_id: str, agent_id: str, worktree: str = "/tmp/worktree", branch: str = "agent/test"):
    temp_db.conn.execute(
        "INSERT INTO merge_queue (issue_id, agent_id, project, worktree, branch_name) VALUES (?, ?, ?, ?, ?)",
        (issue_id, agent_id, "test", worktree, branch),
    )
    temp_db.conn.commit()


@pytest.mark.parametrize("policy", ["mechanical_then_refinery", "refinery_first", "manual"])
def test_parse_merge_policy_accepts_only_canonical_values(policy):
    """INV-1: only canonical policy names are accepted."""
    assert parse_merge_policy(policy) == policy


@pytest.mark.parametrize("invalid", ["refinery_review_then_merge", "mechanical-refinery", "refineryfirst"])
def test_parse_merge_policy_rejects_legacy_and_unknown_values(invalid):
    """INV-2: legacy/unknown policy names are invalid."""
    with pytest.raises(ValueError, match="Invalid merge policy"):
        parse_merge_policy(invalid)


def test_config_fails_fast_on_invalid_env_policy(monkeypatch):
    """Invalid env policy should fail during config load with actionable error."""
    monkeypatch.setenv("HIVE_MERGE_POLICY", "refinery_review_then_merge")

    with pytest.raises(ValueError, match="HIVE_MERGE_POLICY"):
        _Config()


@pytest.mark.asyncio
async def test_refinery_first_policy_propagates_from_config_to_merge_routing(temp_db, mock_opencode):
    """INV-3: merge routing consumes the same policy token emitted by config."""
    issue_id = temp_db.create_issue("Policy contract", project="test")
    temp_db.update_issue_status(issue_id, "done")
    agent_id = temp_db.create_agent("worker-policy")
    _insert_queued_entry(temp_db, issue_id, agent_id)

    mp = MergeProcessor(temp_db, mock_opencode, "/tmp/project", "test")

    with (
        patch.object(mp, "_try_mechanical_merge", new_callable=AsyncMock) as mock_mechanical,
        patch.object(mp, "_send_to_refinery", new_callable=AsyncMock) as mock_refinery,
        patch("hive.merge.get_worktree_dirty_status_async", new_callable=AsyncMock, return_value=(False, "")),
        patch("hive.merge.Config.MERGE_POLICY", "refinery_first"),
    ):
        await mp.process_queue_once()

    mock_mechanical.assert_not_called()
    mock_refinery.assert_awaited_once()
    assert mock_refinery.await_args.kwargs["merge_policy"] == "refinery_first"

    events = temp_db.get_events(issue_id=issue_id, event_type="merge_started")
    assert len(events) == 1
    detail = json.loads(events[0]["detail"])
    assert detail["merge_policy"] == "refinery_first"


@pytest.mark.asyncio
async def test_merge_routing_rejects_invalid_policy_immediately(temp_db, mock_opencode):
    """Invalid policy should fail fast before merge processing work starts."""
    mp = MergeProcessor(temp_db, mock_opencode, "/tmp/project", "test")

    with patch("hive.merge.Config.MERGE_POLICY", "refinery_review_then_merge"):
        with pytest.raises(ValueError, match="Invalid merge policy"):
            await mp.process_queue_once()
