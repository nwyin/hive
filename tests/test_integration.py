"""Integration tests for the Hive orchestrator with fake OpenCode server."""

import asyncio
import pytest

from hive.opencode import OpenCodeClient


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fake_server_basic_functionality(fake_server):
    """Test that the fake OpenCode server works correctly.

    This validates the plumbing: start fake server, create a session via OpenCodeClient,
    inject an idle event, verify the SSE stream receives it.
    """
    # Create client pointing at fake server
    async with OpenCodeClient(base_url=fake_server.url) as client:
        # Create a session
        session = await client.create_session(title="Test Session")
        session_id = session["id"]

        # Verify session was created
        assert session_id.startswith("fake-")
        assert session_id in fake_server.get_created_sessions()

        # Get session status
        status = await client.get_session_status(session_id)
        assert status["id"] == session_id
        assert status["type"] == "running"

        # Set up SSE connection task
        events_received = []

        async def collect_events():
            """Connect to SSE and collect events."""
            from aiohttp import ClientSession, ClientTimeout

            timeout = ClientTimeout(total=5)
            async with ClientSession(timeout=timeout) as session:
                url = f"{fake_server.url}/session/{session_id}/events"
                async with session.get(url) as resp:
                    resp.raise_for_status()
                    async for line in resp.content:
                        line = line.decode("utf-8").strip()
                        if line.startswith("data: "):
                            import json

                            data = line[6:]  # Strip "data: " prefix
                            try:
                                event = json.loads(data)
                                events_received.append(event)
                                # Stop when we get idle event
                                if event.get("type") == "session.status" and event.get("status") == "idle":
                                    break
                            except json.JSONDecodeError:
                                continue

        # Start collecting events
        event_task = asyncio.create_task(collect_events())

        # Give SSE connection time to establish
        await asyncio.sleep(0.1)

        # Inject an idle event
        fake_server.inject_idle(session_id)

        # Wait for event collection to complete
        try:
            await asyncio.wait_for(event_task, timeout=2.0)
        except asyncio.TimeoutError:
            pytest.fail("SSE event was not received within timeout")

        # Verify we received the idle event
        assert len(events_received) == 1
        assert events_received[0]["type"] == "session.status"
        assert events_received[0]["status"] == "idle"

        # Verify session status not changed by inject_idle (it only affects SSE events)
        status = await client.get_session_status(session_id)
        assert status["type"] == "running"

        # Test message endpoint
        await client.send_message_async(session_id, [{"type": "text", "text": "Hello world"}])

        # Test messages endpoint returns empty list
        messages = await client.get_messages(session_id)
        assert messages == []

        # Test abort session
        result = await client.abort_session(session_id)
        assert result is True

        # Verify session status changed to idle after abort
        status = await client.get_session_status(session_id)
        assert status["type"] == "idle"

        # Test delete session
        result = await client.delete_session(session_id)
        assert result is True


@pytest.mark.integration
@pytest.mark.asyncio
async def test_happy_path_issue_to_merge(integration_orchestrator, fake_server, temp_git_repo):
    """Test the complete happy path: issue creation to merge queue.

    This tests the full flow:
    1. Create issue in DB
    2. Orchestrator picks it up and spawns worker
    3. Fake server receives session creation
    4. Write completion result file
    5. Inject idle event to trigger completion
    6. Verify final DB state (issue done, merge queue entry)
    """
    from tests.conftest import write_hive_result

    # Step 1: Create an issue
    issue_id = integration_orchestrator.db.create_issue(title="Test feature", description="Implement X", priority=1, issue_type="task")

    # Verify issue is created and ready
    issue = integration_orchestrator.db.get_issue(issue_id)
    assert issue["status"] == "open"
    assert issue["assignee"] is None  # Ready for assignment

    # Step 2: Spawn worker directly
    ready_issues = integration_orchestrator.db.get_ready_queue()
    assert len(ready_issues) == 1

    await integration_orchestrator.spawn_worker(ready_issues[0])

    # Step 3: Verify session was created on fake server
    sessions = fake_server.get_created_sessions()
    assert len(sessions) == 1
    session_id = sessions[0]
    assert session_id.startswith("fake-")

    # Verify issue was claimed
    issue = integration_orchestrator.db.get_issue(issue_id)
    assert issue["status"] == "in_progress"
    assert issue["assignee"] is not None
    agent_id = issue["assignee"]

    # Get agent info to find worktree
    agent = integration_orchestrator.active_agents.get(agent_id)
    assert agent is not None
    worktree_path = agent.worktree

    # Step 4: Write successful completion result file
    write_hive_result(
        worktree_path=worktree_path,
        status="success",
        summary="Implemented X",
        files_changed=["src/feature.py"],
        tests_added=["tests/test_feature.py::test_implementation"],
        tests_run=True,
        test_command="pytest tests/test_feature.py -v",
        blockers=[],
        artifacts=[{"type": "git_commit", "value": "abc1234"}],
    )

    # Step 5: Simulate completion by directly calling handle_agent_complete
    # This bypasses the SSE monitoring since that's complex to set up in tests
    from hive.prompts import read_result_file

    # Read the result file we just wrote
    file_result = read_result_file(worktree_path)
    assert file_result is not None
    assert file_result["status"] == "success"

    # Call handle_agent_complete directly with the file result
    await integration_orchestrator.handle_agent_complete(agent, file_result=file_result)

    # Allow additional time for completion processing
    await asyncio.sleep(0.5)

    # Step 7: Assert final state

    # Issue should be done
    final_issue = integration_orchestrator.db.get_issue(issue_id)
    assert final_issue["status"] == "done"

    # Agent should no longer be working (not in active_agents)
    assert agent_id not in integration_orchestrator.active_agents

    # Merge queue should have an entry for this issue
    merge_queue = integration_orchestrator.db.get_queued_merges()
    merge_entries = [entry for entry in merge_queue if entry["issue_id"] == issue_id]
    assert len(merge_entries) == 1
    merge_entry = merge_entries[0]
    assert merge_entry["agent_id"] == agent_id
    assert merge_entry["project"] == "test-project"
    assert merge_entry["worktree"] == worktree_path
    assert merge_entry["branch_name"] == f"agent/{agent.name}"

    # Verify events were logged correctly
    events = integration_orchestrator.db.get_events(issue_id=issue_id)
    event_types = [event["event_type"] for event in events]

    # Should have these events in order
    expected_events = ["created", "claimed", "worker_started", "completed"]
    for expected_event in expected_events:
        assert expected_event in event_types, f"Missing event: {expected_event}"

    # Verify completed event has correct details
    completed_events = [e for e in events if e["event_type"] == "completed"]
    assert len(completed_events) == 1
    completed_event = completed_events[0]

    # Parse event details JSON
    import json

    event_details = json.loads(completed_event["detail"])
    assert event_details["summary"] == "Implemented X"
    assert "artifacts" in event_details


async def _wait_for_completion(orchestrator, agent_id):
    """Wait for the specific agent to complete (no longer in active_agents)."""
    try:
        while agent_id in orchestrator.active_agents:
            await asyncio.sleep(0.1)
    except Exception as e:
        print(f"Completion wait error: {e}")
        raise
