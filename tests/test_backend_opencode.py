"""Tests for OpenCode backend - merged opencode.py + sse.py implementation."""

import pytest

from hive.backend_opencode import OpenCodeBackend, SSEWatcher, make_model_config


class TestOpenCodeBackend:
    """Test OpenCodeBackend class functionality."""

    @pytest.mark.asyncio
    async def test_initialization(self):
        """Test backend initialization."""
        backend = OpenCodeBackend(
            base_url="http://localhost:4096", password="test123", global_events=True, directory="/tmp/test", enable_sse=False
        )

        assert backend.base_url == "http://localhost:4096"
        assert backend.password == "test123"
        assert backend.global_events is True
        assert backend.directory == "/tmp/test"
        assert backend.enable_sse is False
        assert backend.session is None
        assert backend._session_dirs == {}
        assert backend._handlers == {}
        assert backend.running is False
        assert backend._sse_task is None

    @pytest.mark.asyncio
    async def test_auth_header(self):
        """Test auth header generation."""
        backend = OpenCodeBackend(password="secret123")
        auth = backend._get_auth_header()

        assert "Authorization" in auth
        assert auth["Authorization"].startswith("Basic ")

    @pytest.mark.asyncio
    async def test_auth_header_no_password(self):
        """Test auth header when no password is set."""
        backend = OpenCodeBackend(password=None)
        auth = backend._get_auth_header()

        assert auth == {}

    @pytest.mark.asyncio
    async def test_directory_header(self):
        """Test directory header generation."""
        backend = OpenCodeBackend()
        header = backend._get_directory_header("/home/user/project")

        assert "X-OpenCode-Directory" in header
        assert header["X-OpenCode-Directory"] == "/home/user/project"

    @pytest.mark.asyncio
    async def test_directory_header_none(self):
        """Test directory header when not specified."""
        backend = OpenCodeBackend()
        header = backend._get_directory_header(None)

        assert "X-OpenCode-Directory" not in header

    @pytest.mark.asyncio
    async def test_context_manager(self):
        """Test async context manager functionality."""
        async with OpenCodeBackend(enable_sse=False) as backend:
            assert backend.session is not None
            assert backend.running is True
            assert backend._sse_task is None  # No SSE task when disabled

        # Should be stopped after context exit
        assert backend.session is None
        assert backend.running is False
        assert backend._sse_task is None

    @pytest.mark.asyncio
    async def test_start_stop_lifecycle(self):
        """Test start/stop lifecycle management."""
        backend = OpenCodeBackend(enable_sse=False)

        # Initially not started
        assert backend.session is None
        assert backend.running is False
        assert backend._sse_task is None

        # Start
        await backend.start()
        assert backend.session is not None
        assert backend.running is True
        assert backend._sse_task is None  # No SSE task when disabled

        # Stop
        await backend.stop()
        assert backend.session is None
        assert backend.running is False
        assert backend._sse_task is None

    @pytest.mark.asyncio
    async def test_session_dirs_mapping(self):
        """Test session_id -> directory mapping functionality."""
        backend = OpenCodeBackend()

        # Initially empty
        assert backend._session_dirs == {}
        assert backend._get_session_directory("test-session") is None

        # Manually add mapping
        backend._session_dirs["test-session"] = "/tmp/test"
        assert backend._get_session_directory("test-session") == "/tmp/test"

        # Removing mapping
        backend._session_dirs.pop("test-session", None)
        assert backend._get_session_directory("test-session") is None

    @pytest.mark.asyncio
    async def test_sse_event_handlers(self):
        """Test SSE event handler registration."""
        backend = OpenCodeBackend()

        events_received = []
        all_events_received = []

        def status_handler(properties):
            events_received.append(("status", properties))

        def all_handler(event_type, properties):
            all_events_received.append((event_type, properties))

        # Register handlers
        backend.on("session.status", status_handler)
        backend.on_all(all_handler)

        assert "session.status" in backend._handlers
        assert "*" in backend._handlers

        # Test event dispatch
        test_event = {"payload": {"type": "session.status", "properties": {"sessionID": "test-123", "status": {"type": "idle"}}}}

        await backend._dispatch_event(test_event)

        # Check both handlers were called
        assert len(events_received) == 1
        assert events_received[0][0] == "status"
        assert events_received[0][1]["sessionID"] == "test-123"

        assert len(all_events_received) == 1
        assert all_events_received[0][0] == "session.status"
        assert all_events_received[0][1]["sessionID"] == "test-123"

    def test_make_model_config(self):
        """Test model config creation utility."""
        config = make_model_config("claude-3-5-sonnet-20241022")
        assert config == {"providerID": "anthropic", "modelID": "claude-3-5-sonnet-20241022"}

        config = make_model_config("gpt-4", "openai")
        assert config == {"providerID": "openai", "modelID": "gpt-4"}


class TestOpenCodeBackendWithFakeServer:
    """Integration tests using FakeOpenCodeServer."""

    @pytest.mark.asyncio
    async def test_create_session_with_directory_mapping(self, fake_server):
        """Test session creation stores directory mapping."""
        async with OpenCodeBackend(base_url=fake_server.url, enable_sse=False) as backend:
            session = await backend.create_session(directory="/tmp/test", title="Test session")

            session_id = session["id"]

            # Verify session was created
            assert "id" in session
            assert session["title"] == "Test session"
            assert session["directory"] == "/tmp/test"

            # Verify directory mapping was stored
            assert backend._get_session_directory(session_id) == "/tmp/test"

            # Clean up
            await backend.delete_session(session_id)

            # Verify mapping was removed after deletion
            assert backend._get_session_directory(session_id) is None

    @pytest.mark.asyncio
    async def test_get_session_status_uses_stored_directory(self, fake_server):
        """Test that get_session_status uses stored directory mapping."""
        async with OpenCodeBackend(base_url=fake_server.url, enable_sse=False) as backend:
            # Create session with directory
            session = await backend.create_session(directory="/tmp/test")
            session_id = session["id"]

            # Get status without providing directory - should use stored mapping
            status = await backend.get_session_status(session_id)

            assert "type" in status
            assert status["type"] in ["idle", "busy", "retry"]

            # Clean up
            await backend.delete_session(session_id)

    @pytest.mark.asyncio
    async def test_send_message_wrapper_functionality(self, fake_server):
        """Test send_message() wraps text into parts and model into config format."""
        async with OpenCodeBackend(base_url=fake_server.url, enable_sse=False) as backend:
            session = await backend.create_session(directory="/tmp/test")
            session_id = session["id"]

            # Test the convenience method that wraps text and model
            await backend.send_message(
                session_id, text="Hello, world!", model="claude-3-5-sonnet-20241022", system="You are a helpful assistant"
            )

            # Verify the message was received by fake server
            messages = fake_server.messages.get(session_id, [])
            assert len(messages) == 1

            message = messages[0]
            assert message["parts"] == [{"type": "text", "text": "Hello, world!"}]
            assert message["model"] == {"providerID": "anthropic", "modelID": "claude-3-5-sonnet-20241022"}
            assert message["system"] == "You are a helpful assistant"

            # Clean up
            await backend.delete_session(session_id)

    @pytest.mark.asyncio
    async def test_send_message_async_direct(self, fake_server):
        """Test send_message_async() directly with parts and model config."""
        async with OpenCodeBackend(base_url=fake_server.url, enable_sse=False) as backend:
            session = await backend.create_session(directory="/tmp/test")
            session_id = session["id"]

            parts = [{"type": "text", "text": "What is 2+2?"}, {"type": "text", "text": "Please explain."}]
            model_config = {"providerID": "anthropic", "modelID": "claude-3-haiku-20240307"}

            await backend.send_message_async(session_id, parts=parts, agent="build", model=model_config, system="Be concise")

            # Verify message structure
            messages = fake_server.messages.get(session_id, [])
            assert len(messages) == 1

            message = messages[0]
            assert message["parts"] == parts
            assert message["model"] == model_config
            assert message["agent"] == "build"
            assert message["system"] == "Be concise"

            # Clean up
            await backend.delete_session(session_id)

    @pytest.mark.asyncio
    async def test_abort_and_delete_session(self, fake_server):
        """Test abort and delete session functionality."""
        async with OpenCodeBackend(base_url=fake_server.url, enable_sse=False) as backend:
            session = await backend.create_session(directory="/tmp/test")
            session_id = session["id"]

            # Abort session
            result = await backend.abort_session(session_id)
            assert result is True

            # Delete session
            result = await backend.delete_session(session_id)
            assert result is True

            # Verify session was removed from fake server
            assert session_id not in fake_server.sessions

    @pytest.mark.asyncio
    async def test_cleanup_session_best_effort(self, fake_server):
        """Test cleanup_session() swallows exceptions."""
        async with OpenCodeBackend(base_url=fake_server.url, enable_sse=False) as backend:
            session = await backend.create_session()
            session_id = session["id"]

            # Should not raise exception even if operations fail
            await backend.cleanup_session(session_id)

            # Should not raise exception even for non-existent session
            await backend.cleanup_session("non-existent-session")

    @pytest.mark.asyncio
    async def test_list_sessions(self, fake_server):
        """Test listing sessions."""
        async with OpenCodeBackend(base_url=fake_server.url, enable_sse=False) as backend:
            # Initially no sessions
            sessions = await backend.list_sessions()
            initial_count = len(sessions)

            # Create a session
            session = await backend.create_session(title="Test List Session")
            session_id = session["id"]

            # Should now have one more session
            sessions = await backend.list_sessions()
            assert len(sessions) == initial_count + 1

            # Find our session
            our_session = next(s for s in sessions if s["id"] == session_id)
            assert our_session["title"] == "Test List Session"

            # Clean up
            await backend.delete_session(session_id)

    @pytest.mark.asyncio
    async def test_get_messages(self, fake_server):
        """Test getting messages from a session."""
        async with OpenCodeBackend(base_url=fake_server.url, enable_sse=False) as backend:
            session = await backend.create_session()
            session_id = session["id"]

            # Send a message
            await backend.send_message(session_id, "Test message")

            # Get messages
            messages = await backend.get_messages(session_id)

            # The fake server should have stored our message
            assert len(messages) >= 1

            # Clean up
            await backend.delete_session(session_id)

    @pytest.mark.asyncio
    async def test_get_pending_permissions(self, fake_server):
        """Test getting pending permissions."""
        async with OpenCodeBackend(base_url=fake_server.url, enable_sse=False) as backend:
            permissions = await backend.get_pending_permissions()
            assert isinstance(permissions, list)

    @pytest.mark.asyncio
    async def test_reply_permission(self, fake_server):
        """Test replying to permission request."""
        async with OpenCodeBackend(base_url=fake_server.url, enable_sse=False) as backend:
            # Should not raise exception (fake server handles gracefully)
            await backend.reply_permission("test-request-123", "allow")

    @pytest.mark.asyncio
    async def test_runtime_error_without_start(self):
        """Test that methods raise RuntimeError when backend not started."""
        backend = OpenCodeBackend()

        with pytest.raises(RuntimeError, match="Backend not started"):
            await backend.list_sessions()

        with pytest.raises(RuntimeError, match="Backend not started"):
            await backend.create_session()

        with pytest.raises(RuntimeError, match="Backend not started"):
            await backend.send_message_async("test", [])

        with pytest.raises(RuntimeError, match="Backend not started"):
            await backend.get_session_status("test")


class TestSSEEventParsing:
    """Test SSE event parsing and dispatch."""

    @pytest.mark.asyncio
    async def test_sse_event_unwrapping(self):
        """Test SSE event unwrapping from OpenCode envelope format."""
        backend = OpenCodeBackend()

        events_received = []

        def handler(properties):
            events_received.append(properties)

        backend.on("session.status", handler)

        # Test wrapped event (typical OpenCode format)
        wrapped_event = {
            "directory": "/tmp/project",
            "payload": {"type": "session.status", "properties": {"sessionID": "test-123", "status": {"type": "busy"}}},
        }

        await backend._dispatch_event(wrapped_event)

        assert len(events_received) == 1
        assert events_received[0]["sessionID"] == "test-123"
        assert events_received[0]["status"]["type"] == "busy"

        # Test unwrapped event (fallback format)
        events_received.clear()
        unwrapped_event = {"type": "session.status", "properties": {"sessionID": "test-456", "status": {"type": "idle"}}}

        await backend._dispatch_event(unwrapped_event)

        assert len(events_received) == 1
        assert events_received[0]["sessionID"] == "test-456"
        assert events_received[0]["status"]["type"] == "idle"

    @pytest.mark.asyncio
    async def test_sync_and_async_handlers(self):
        """Test both sync and async event handlers work."""
        backend = OpenCodeBackend()

        sync_events = []
        async_events = []

        def sync_handler(properties):
            sync_events.append(properties)

        async def async_handler(properties):
            async_events.append(properties)

        backend.on("test.sync", sync_handler)
        backend.on("test.async", async_handler)

        # Dispatch events
        await backend._dispatch_event({"payload": {"type": "test.sync", "properties": {"data": "sync_data"}}})

        await backend._dispatch_event({"payload": {"type": "test.async", "properties": {"data": "async_data"}}})

        assert len(sync_events) == 1
        assert sync_events[0]["data"] == "sync_data"

        assert len(async_events) == 1
        assert async_events[0]["data"] == "async_data"


class TestSSEWatcher:
    """Test SSEWatcher utility class."""

    def test_initialization(self):
        """Test SSEWatcher initialization."""
        watcher = SSEWatcher(base_url="http://localhost:4096", password="test123", global_events=False, directory="/tmp/watch")

        assert watcher.base_url == "http://localhost:4096"
        assert watcher.password == "test123"
        assert watcher.global_events is False
        assert watcher.directory == "/tmp/watch"
        assert watcher._handlers == {}
        assert watcher.running is False

    def test_event_handler_registration(self):
        """Test event handler registration in SSEWatcher."""
        watcher = SSEWatcher()

        def status_handler(properties):
            pass

        def all_handler(event_type, properties):
            pass

        watcher.on("session.status", status_handler)
        watcher.on_all(all_handler)

        assert "session.status" in watcher._handlers
        assert "*" in watcher._handlers

    @pytest.mark.asyncio
    async def test_sse_watcher_event_dispatch(self):
        """Test SSEWatcher event dispatch."""
        watcher = SSEWatcher()

        events_received = []
        all_events_received = []

        def status_handler(properties):
            events_received.append(properties)

        def all_handler(event_type, properties):
            all_events_received.append((event_type, properties))

        watcher.on("session.status", status_handler)
        watcher.on_all(all_handler)

        # Dispatch event
        test_event = {"payload": {"type": "session.status", "properties": {"sessionID": "watch-123", "status": {"type": "idle"}}}}

        await watcher._dispatch_event(test_event)

        # Check handlers were called
        assert len(events_received) == 1
        assert events_received[0]["sessionID"] == "watch-123"

        assert len(all_events_received) == 1
        assert all_events_received[0][0] == "session.status"
        assert all_events_received[0][1]["sessionID"] == "watch-123"

    def test_stop_functionality(self):
        """Test SSEWatcher stop functionality."""
        watcher = SSEWatcher()

        watcher.running = True
        watcher.stop()

        assert watcher.running is False
