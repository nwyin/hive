"""Backend implementations for Hive.

Backends available:
- Claude: Direct WebSocket to Claude CLI processes (--sdk-url)
- Codex: Local `codex app-server` over stdio (JSON-RPC)
"""

from .backend_claude import ClaudeWSBackend, SessionState
from .backend_codex import CodexAppServerBackend
from .base import HiveBackend

__all__ = [
    "ClaudeWSBackend",
    "CodexAppServerBackend",
    "HiveBackend",
    "SessionState",
]
