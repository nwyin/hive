"""Backend implementations for Hive.

Backends available:
- OpenCode: HTTP REST + SSE via an external OpenCode server
- Claude: Direct WebSocket to Claude CLI processes (--sdk-url)
- Codex: Local `codex app-server` over stdio (JSON-RPC)
"""

from .backend_claude import ClaudeWSBackend, SessionState
from .backend_codex import CodexAppServerBackend
from .backend_opencode import OpenCodeClient, SSEClient, make_model_config
from .base import HiveBackend

__all__ = [
    "ClaudeWSBackend",
    "CodexAppServerBackend",
    "HiveBackend",
    "OpenCodeClient",
    "SSEClient",
    "SessionState",
    "make_model_config",
]
