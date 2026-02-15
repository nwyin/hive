"""OpenCode backend - merges opencode.py + sse.py into one self-contained module."""

import asyncio
import base64
import inspect
import json
import logging
import os
from typing import Any, Callable, Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)


def make_model_config(model_id: str, provider_id: str = "anthropic") -> Dict[str, str]:
    """Build a model config dict from a model ID string."""
    return {"providerID": provider_id, "modelID": model_id}


class OpenCodeBackend:
    """
    Self-contained Backend implementation that merges OpenCode HTTP client + SSE event stream.

    Implements both OpenCodeClient and SSEClient interfaces for drop-in replacement.
    Makes HTTP calls directly via aiohttp without delegation layer.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:4096",
        password: Optional[str] = None,
        global_events: bool = True,
        directory: Optional[str] = None,
        enable_sse: bool = True,
    ):
        """
        Initialize OpenCode backend.

        Args:
            base_url: Base URL of OpenCode server
            password: Server password (reads from OPENCODE_SERVER_PASSWORD if not provided)
            global_events: If True, connect to /global/event; else /event for SSE
            directory: Directory to scope SSE events to (only for /event endpoint)
            enable_sse: If False, skip SSE connection (for testing HTTP-only functionality)
        """
        self.base_url = base_url.rstrip("/")
        self.password = password or os.environ.get("OPENCODE_SERVER_PASSWORD")
        self.global_events = global_events
        self.directory = directory
        self.enable_sse = enable_sse

        # HTTP session for API calls
        self.session: Optional[aiohttp.ClientSession] = None

        # Session ID -> directory mapping for automatic header injection
        self._session_dirs: Dict[str, str] = {}

        # SSE-compatible event handlers
        self._handlers: Dict[str, Callable] = {}

        # SSE connection state
        self.running = False
        self._sse_task: Optional[asyncio.Task] = None

    def _get_auth_header(self) -> Dict[str, str]:
        """Generate Authorization header for HTTP Basic Auth."""
        if not self.password:
            return {}

        username = os.environ.get("OPENCODE_SERVER_USERNAME", "opencode")
        credentials = f"{username}:{self.password}"
        encoded = base64.b64encode(credentials.encode()).decode()
        return {"Authorization": f"Basic {encoded}"}

    def _get_directory_header(self, directory: Optional[str]) -> Dict[str, str]:
        """Generate X-OpenCode-Directory header if directory is specified."""
        if directory:
            return {"X-OpenCode-Directory": directory}
        return {}

    def _get_session_directory(self, session_id: str) -> Optional[str]:
        """Get stored directory for session_id, if any."""
        return self._session_dirs.get(session_id)

    # ── OpenCodeClient-compatible methods ─────────────────────────────

    async def __aenter__(self):
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.stop()

    async def start(self):
        """Start the backend: create HTTP session and spawn SSE reconnect loop."""
        if self.session:
            return  # Already started

        timeout = aiohttp.ClientTimeout(total=30)
        self.session = aiohttp.ClientSession(timeout=timeout)

        # Start SSE reconnect loop (if enabled)
        self.running = True
        if self.enable_sse:
            self._sse_task = asyncio.create_task(self._sse_reconnect_loop())

    async def stop(self):
        """Stop the backend: cancel SSE task and close HTTP session."""
        # Stop SSE connection
        self.running = False
        if self._sse_task and not self._sse_task.done():
            self._sse_task.cancel()
            try:
                await self._sse_task
            except asyncio.CancelledError:
                pass
        self._sse_task = None

        # Close HTTP session
        if self.session:
            await self.session.close()
            self.session = None

    async def list_sessions(self) -> List[Dict[str, Any]]:
        """
        List all sessions on the OpenCode server.

        Returns:
            List of session dicts with id, title, etc.
        """
        if not self.session:
            raise RuntimeError("Backend not started. Call start() or use async with context manager.")

        headers = {**self._get_auth_header()}

        url = f"{self.base_url}/session"
        async with self.session.get(url, headers=headers) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def create_session(
        self,
        directory: Optional[str] = None,
        title: Optional[str] = None,
        permissions: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """
        Create a new OpenCode session.

        Args:
            directory: Project directory to scope the session to
            title: Session title (auto-generated if omitted)
            permissions: Permission rules for the session

        Returns:
            Session info dict with id, title, directory, etc.
        """
        if not self.session:
            raise RuntimeError("Backend not started. Call start() or use async with context manager.")

        headers = {
            **self._get_auth_header(),
            **self._get_directory_header(directory),
            "Content-Type": "application/json",
        }

        payload = {}
        if title:
            payload["title"] = title
        if permissions:
            payload["permission"] = permissions

        url = f"{self.base_url}/session"
        async with self.session.post(url, json=payload, headers=headers) as resp:
            resp.raise_for_status()
            session_data = await resp.json()

            # Store session_id -> directory mapping for automatic header injection
            session_id = session_data.get("id")
            if session_id and directory:
                self._session_dirs[session_id] = directory

            return session_data

    async def send_message_async(
        self,
        session_id: str,
        parts: List[Dict[str, Any]],
        agent: str = "build",
        model: Optional[Dict[str, str]] = None,
        system: Optional[str] = None,
        directory: Optional[str] = None,
    ):
        """
        Send a message asynchronously (fire-and-forget).

        Returns immediately with HTTP 204. Monitor progress via SSE.

        Args:
            session_id: Session ID
            parts: Message parts
            agent: Agent type (default: "build")
            model: Model config dict with providerID and modelID
            system: Additional system prompt
            directory: Directory context for this request
        """
        if not self.session:
            raise RuntimeError("Backend not started. Call start() or use async with context manager.")

        # Use stored directory if not provided
        if not directory:
            directory = self._get_session_directory(session_id)

        headers = {
            **self._get_auth_header(),
            **self._get_directory_header(directory),
            "Content-Type": "application/json",
        }

        payload = {"parts": parts, "agent": agent}
        if model:
            payload["model"] = model
        if system:
            payload["system"] = system

        url = f"{self.base_url}/session/{session_id}/prompt_async"
        async with self.session.post(url, json=payload, headers=headers) as resp:
            resp.raise_for_status()

    def send_message(
        self,
        session_id: str,
        text: str,
        model: Optional[str] = None,
        system: Optional[str] = None,
        directory: Optional[str] = None,
    ):
        """
        Send a message (convenience method that wraps text into parts list and model format).

        Args:
            session_id: Session ID
            text: Plain text message
            model: Plain model string (e.g., "claude-3-5-sonnet-20241022") - will be wrapped into model config
            system: Additional system prompt
            directory: Directory context for this request
        """
        # Wrap plain text into parts list
        parts = [{"type": "text", "text": text}]

        # Wrap plain model string into {providerID, modelID} format
        model_config = None
        if model:
            model_config = make_model_config(model)

        return self.send_message_async(session_id, parts, model=model_config, system=system, directory=directory)

    async def abort_session(self, session_id: str, directory: Optional[str] = None) -> bool:
        """
        Abort a running session.

        Args:
            session_id: Session ID to abort
            directory: Directory context

        Returns:
            True if aborted successfully
        """
        if not self.session:
            raise RuntimeError("Backend not started. Call start() or use async with context manager.")

        # Use stored directory if not provided
        if not directory:
            directory = self._get_session_directory(session_id)

        headers = {
            **self._get_auth_header(),
            **self._get_directory_header(directory),
        }

        url = f"{self.base_url}/session/{session_id}/abort"
        async with self.session.post(url, headers=headers) as resp:
            return resp.status == 200

    async def delete_session(self, session_id: str, directory: Optional[str] = None) -> bool:
        """
        Delete a session.

        Args:
            session_id: Session ID to delete
            directory: Directory context

        Returns:
            True if deleted successfully
        """
        if not self.session:
            raise RuntimeError("Backend not started. Call start() or use async with context manager.")

        # Use stored directory if not provided
        if not directory:
            directory = self._get_session_directory(session_id)

        headers = {
            **self._get_auth_header(),
            **self._get_directory_header(directory),
        }

        url = f"{self.base_url}/session/{session_id}"
        async with self.session.delete(url, headers=headers) as resp:
            success = resp.status == 200

            # Remove from session directory mapping
            if success:
                self._session_dirs.pop(session_id, None)

            return success

    async def get_session_status(self, session_id: str, directory: Optional[str] = None) -> Dict[str, Any]:
        """
        Get session status (idle/busy/retry).

        Args:
            session_id: Session ID
            directory: Directory context

        Returns:
            Status dict with type field
        """
        if not self.session:
            raise RuntimeError("Backend not started. Call start() or use async with context manager.")

        # Use stored directory if not provided
        if not directory:
            directory = self._get_session_directory(session_id)

        headers = {
            **self._get_auth_header(),
            **self._get_directory_header(directory),
        }

        url = f"{self.base_url}/session/{session_id}/status"
        async with self.session.get(url, headers=headers) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def get_messages(
        self,
        session_id: str,
        directory: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get all messages in a session.

        Args:
            session_id: Session ID
            directory: Directory context
            limit: Maximum number of messages to return

        Returns:
            List of message dicts
        """
        if not self.session:
            raise RuntimeError("Backend not started. Call start() or use async with context manager.")

        # Use stored directory if not provided
        if not directory:
            directory = self._get_session_directory(session_id)

        headers = {
            **self._get_auth_header(),
            **self._get_directory_header(directory),
        }

        url = f"{self.base_url}/session/{session_id}/message"
        if limit:
            url += f"?limit={limit}"

        async with self.session.get(url, headers=headers) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def get_pending_permissions(self, directory: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get all pending permission requests.

        Args:
            directory: Filter by directory

        Returns:
            List of pending permission request dicts
        """
        if not self.session:
            raise RuntimeError("Backend not started. Call start() or use async with context manager.")

        headers = {
            **self._get_auth_header(),
            **self._get_directory_header(directory),
        }

        url = f"{self.base_url}/permission"
        async with self.session.get(url, headers=headers) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def reply_permission(
        self,
        request_id: str,
        reply: str,
        message: Optional[str] = None,
        directory: Optional[str] = None,
    ):
        """
        Reply to a permission request.

        Args:
            request_id: Permission request ID
            reply: "once", "always", or "reject"
            message: Optional message when rejecting
            directory: Directory context
        """
        if not self.session:
            raise RuntimeError("Backend not started. Call start() or use async with context manager.")

        headers = {
            **self._get_auth_header(),
            **self._get_directory_header(directory),
            "Content-Type": "application/json",
        }

        payload = {"reply": reply}
        if message:
            payload["message"] = message

        url = f"{self.base_url}/permission/{request_id}/reply"
        async with self.session.post(url, json=payload, headers=headers) as resp:
            resp.raise_for_status()

    async def cleanup_session(self, session_id: str, directory: Optional[str] = None):
        """Abort and delete a session. Best-effort — exceptions are swallowed."""
        try:
            await self.abort_session(session_id, directory=directory)
        except Exception:
            pass
        try:
            await self.delete_session(session_id, directory=directory)
        except Exception:
            pass

    # ── SSEClient-compatible methods ──────────────────────────────────

    def on(self, event_type: str, handler: Callable[[Dict[str, Any]], None]):
        """
        Register an event handler.

        Args:
            event_type: Event type to listen for (e.g., "session.status")
            handler: Async or sync callback function that receives event properties
        """
        self._handlers[event_type] = handler

    def on_all(self, handler: Callable[[str, Dict[str, Any]], None]):
        """
        Register a catch-all event handler.

        Args:
            handler: Async or sync callback function that receives (event_type, properties)
        """
        self._handlers["*"] = handler

    async def connect_with_reconnect(self, max_retries: int = -1, retry_delay: int = 5):
        """
        Connect with automatic reconnection on failure (compatibility method).

        The actual SSE connection is managed internally via _sse_reconnect_loop()
        which is started by start(). This method just waits while running.

        Args:
            max_retries: Maximum retry attempts (-1 for infinite)
            retry_delay: Seconds to wait between retries
        """
        while self.running:
            await asyncio.sleep(1)

    # ── Internal: SSE connection and parsing (~80 lines) ──────────────

    async def _sse_reconnect_loop(self, max_retries: int = -1, retry_delay: int = 5):
        """
        Internal SSE reconnect loop with automatic reconnection on failure.

        Args:
            max_retries: Maximum retry attempts (-1 for infinite)
            retry_delay: Seconds to wait between retries
        """
        retries = 0
        while self.running and (max_retries < 0 or retries < max_retries):
            try:
                await self._sse_connect()
                retries = 0  # Reset counter on successful connection
            except Exception as e:
                if not self.running:
                    break

                retries += 1
                if max_retries >= 0 and retries >= max_retries:
                    logger.error(f"SSE max retries ({max_retries}) exceeded: {e}")
                    break

                logger.warning(f"SSE connection failed (attempt {retries}), retrying in {retry_delay}s: {e}")
                await asyncio.sleep(retry_delay)

    async def _sse_connect(self):
        """
        Connect to SSE stream and start consuming events.

        This method will run until self.running is False or the connection fails.
        """
        if self.global_events:
            url = f"{self.base_url}/global/event"
        else:
            url = f"{self.base_url}/event"
            if self.directory:
                url += f"?directory={self.directory}"

        headers = {}
        if self.password:
            username = os.environ.get("OPENCODE_SERVER_USERNAME", "opencode")
            credentials = f"{username}:{self.password}"
            encoded = base64.b64encode(credentials.encode()).decode()
            headers["Authorization"] = f"Basic {encoded}"

        timeout = aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=30)

        # Use a separate session for SSE to avoid conflicts with HTTP API calls
        async with aiohttp.ClientSession(timeout=timeout) as sse_session:
            async with sse_session.get(url, headers=headers) as resp:
                resp.raise_for_status()

                async for line in resp.content:
                    if not self.running:
                        break

                    line = line.decode("utf-8").strip()

                    # SSE format: "data: {json}"
                    if line.startswith("data: "):
                        data = line[6:]  # Strip "data: " prefix
                        try:
                            event = json.loads(data)
                            await self._dispatch_event(event)
                        except json.JSONDecodeError:
                            # Skip malformed events
                            continue

    async def _dispatch_event(self, event: Dict[str, Any]):
        """
        Dispatch an event to registered handlers.

        OpenCode wraps events in a payload envelope:
            {"directory": "...", "payload": {"type": "...", "properties": {...}}}
        Unwrap if present, otherwise fall back to top-level fields.
        """
        payload = event.get("payload", event)
        event_type = payload.get("type")
        properties = payload.get("properties", {})

        # Call specific handler if registered
        if event_type in self._handlers:
            handler = self._handlers[event_type]
            await self._call_handler(handler, properties)

        # Call catch-all handler if registered
        if "*" in self._handlers:
            handler = self._handlers["*"]
            await self._call_handler(handler, event_type, properties)

    async def _call_handler(self, handler: Callable, *args):
        """Call a handler, handling both sync and async functions."""
        try:
            if inspect.iscoroutinefunction(handler):
                await handler(*args)
            else:
                handler(*args)
        except Exception as e:
            logger.error(f"Error in event handler: {e}")

    def _emit(self, event_type: str, properties: Dict[str, Any]):
        """
        Emit an event to registered handlers (sync version for internal use).

        Args:
            event_type: Event type to emit
            properties: Event properties dict
        """
        # This is a sync wrapper that schedules the async dispatch
        # Used for compatibility with code that expects sync _emit
        asyncio.create_task(self._dispatch_event({"payload": {"type": event_type, "properties": properties}}))


class SSEWatcher:
    """
    Lightweight read-only utility for CLI watch command.

    Does not implement full Backend interface - just connects to SSE stream
    and emits events for watching/monitoring purposes.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:4096",
        password: Optional[str] = None,
        global_events: bool = True,
        directory: Optional[str] = None,
    ):
        """
        Initialize SSE watcher.

        Args:
            base_url: Base URL of OpenCode server
            password: Server password
            global_events: If True, connect to /global/event; else /event
            directory: Directory to scope events to (only for /event endpoint)
        """
        self.base_url = base_url.rstrip("/")
        self.password = password or os.environ.get("OPENCODE_SERVER_PASSWORD")
        self.global_events = global_events
        self.directory = directory
        self._handlers: Dict[str, Callable] = {}
        self.running = False

    def on(self, event_type: str, handler: Callable[[Dict[str, Any]], None]):
        """Register an event handler."""
        self._handlers[event_type] = handler

    def on_all(self, handler: Callable[[str, Dict[str, Any]], None]):
        """Register a catch-all event handler."""
        self._handlers["*"] = handler

    async def connect_with_reconnect(self, max_retries: int = -1, retry_delay: int = 5):
        """
        Connect with automatic reconnection on failure.

        Args:
            max_retries: Maximum retry attempts (-1 for infinite)
            retry_delay: Seconds to wait between retries
        """
        self.running = True
        retries = 0
        while self.running and (max_retries < 0 or retries < max_retries):
            try:
                await self._connect()
                retries = 0  # Reset counter on successful connection
            except Exception as e:
                if not self.running:
                    break

                retries += 1
                if max_retries >= 0 and retries >= max_retries:
                    logger.error(f"SSE watcher max retries ({max_retries}) exceeded: {e}")
                    break

                logger.warning(f"SSE watcher connection failed (attempt {retries}), retrying in {retry_delay}s: {e}")
                await asyncio.sleep(retry_delay)

    async def _connect(self):
        """Connect to SSE stream and consume events."""
        if self.global_events:
            url = f"{self.base_url}/global/event"
        else:
            url = f"{self.base_url}/event"
            if self.directory:
                url += f"?directory={self.directory}"

        headers = {}
        if self.password:
            username = os.environ.get("OPENCODE_SERVER_USERNAME", "opencode")
            credentials = f"{username}:{self.password}"
            encoded = base64.b64encode(credentials.encode()).decode()
            headers["Authorization"] = f"Basic {encoded}"

        timeout = aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=30)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                resp.raise_for_status()

                async for line in resp.content:
                    if not self.running:
                        break

                    line = line.decode("utf-8").strip()

                    # SSE format: "data: {json}"
                    if line.startswith("data: "):
                        data = line[6:]  # Strip "data: " prefix
                        try:
                            event = json.loads(data)
                            await self._dispatch_event(event)
                        except json.JSONDecodeError:
                            # Skip malformed events
                            continue

    async def _dispatch_event(self, event: Dict[str, Any]):
        """Dispatch an event to registered handlers."""
        payload = event.get("payload", event)
        event_type = payload.get("type")
        properties = payload.get("properties", {})

        # Call specific handler if registered
        if event_type in self._handlers:
            handler = self._handlers[event_type]
            try:
                if inspect.iscoroutinefunction(handler):
                    await handler(properties)
                else:
                    handler(properties)
            except Exception as e:
                logger.error(f"Error in SSE watcher handler: {e}")

        # Call catch-all handler if registered
        if "*" in self._handlers:
            handler = self._handlers["*"]
            try:
                if inspect.iscoroutinefunction(handler):
                    await handler(event_type, properties)
                else:
                    handler(event_type, properties)
            except Exception as e:
                logger.error(f"Error in SSE watcher catch-all handler: {e}")

    def stop(self):
        """Stop consuming events."""
        self.running = False
