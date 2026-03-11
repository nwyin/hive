# Project Context — Hive

## Overview
Lightweight multi-agent orchestrator that dispatches coding tasks to Claude/Codex LLM workers via git worktrees, with a refinery LLM reviewing and merging completed branches.

## Architecture
- **Orchestrator** (`src/hive/orchestrator/`): Main loop polls DB for ready issues, spawns workers into git worktrees, monitors sessions via SSE/WS events, handles stalls/retries/escalation. Split into `core.py` (state, event handlers, main loop), `lifecycle.py` (spawn/stall logic), `completion.py` (result assessment, retry/merge routing).
- **Backends** (`src/hive/backends/`): Abstract `HiveBackend` interface with two implementations — `ClaudeWSBackend` (WebSocket to Claude CLI via `--sdk-url`) and `CodexAppServerBackend` (JSON-RPC stdio to `codex app-server`). Backends handle session CRUD and event streaming.
- **Merge/Refinery** (`src/hive/merge.py`): `MergeProcessorPool` (one `MergeProcessor` per project) runs a persistent refinery LLM session. Pipeline: rebase → test → review → ff-merge to main → finalize. Session auto-cycles when token threshold exceeded.
- **Database** (`src/hive/db/`): SQLite via `sqlite3.Row`. Mixin composition: `DatabaseCore` (schema, connect, events, projects), `IssuesMixin` (CRUD, status transitions via CAS), `NotesMixin`, `MetricsMixin`. DB at `~/.hive/hive.db`.
- **CLI** (`src/hive/cli/`): Typer-based flat command structure. `parser.py` → `typer_app.py` → `runtime.py` (command implementations). `queen.py` for decomposition commands. `formatters.py`/`rich_views.py` for display.
- **Data flow**: User creates issues via CLI → daemon polls DB for open issues → spawns worker in git worktree → worker writes `.hive-result.jsonl` → orchestrator reads result → routes to merge queue → refinery reviews/tests/merges → issue finalized.

## Key Files
- `src/hive/orchestrator/core.py` — Main orchestration loop, SSE handlers, reconciliation, permission policy
- `src/hive/orchestrator/lifecycle.py` — Worker spawn, stall detection, lease renewal
- `src/hive/orchestrator/completion.py` — Result assessment, retry/escalate/merge routing
- `src/hive/merge.py` — MergeProcessor and MergeProcessorPool: refinery session, rebase, test, merge pipeline
- `src/hive/backends/base.py` — HiveBackend ABC: session management + event streaming interface
- `src/hive/backends/backend_claude.py` — Claude WebSocket backend (primary)
- `src/hive/backends/backend_codex.py` — Codex app-server JSON-RPC backend
- `src/hive/db/core.py` — Schema, connection, events table, project registration
- `src/hive/db/issues.py` — Issue CRUD, CAS status transitions, ready queue, epic support
- `src/hive/config.py` — Layered config: defaults → `~/.hive/config.toml` → `.hive.toml` → env vars
- `src/hive/git.py` — Git worktree create/remove, rebase, merge, branch ops (sync + async wrappers)
- `src/hive/prompts.py` — Prompt builders for worker/refinery, `.hive-result.jsonl` and `.hive-notes.jsonl` IO
- `src/hive/daemon.py` — Daemon lifecycle: PID management, orphan detection, foreground runner
- `src/hive/cli/typer_app.py` — Typer app with all CLI commands
- `src/hive/cli/runtime.py` — Command implementations (create, list, start, stop, etc.)
- `tests/conftest.py` — Fixtures: temp_db, fake_backend, integration_orchestrator, test helpers

## Build & Test
- **Language**: Python >=3.12
- **Package manager**: `uv` — install with `uv sync --dev`
- **Build**: `hatchling` (`uv build`)
- **Test**: `python -m pytest tests/ -v --timeout=30` (default runs unit tests with `-n auto` parallelism, skips integration)
- **Integration tests**: `python -m pytest tests/ -v -m integration --timeout=30`
- **Lint**: `uvx ruff check`
- **Format**: `uvx ruff format` (line-length=144)
- **Type check**: `uvx ty check` (python-version=3.12, ignores `unresolved-attribute`)
- **Pre-commit**: N/A
- **Quirks**: `pytest.ini` sets `addopts = -v -m "not integration" -n auto` — integration tests require explicit `-m integration`. Tests use `FakeBackend` (in-memory, no real LLM calls). DB fixtures use temp files, not in-memory SQLite.

## Conventions
- Flat CLI commands — `hive list`, not `hive issues list`
- Mixin composition for large classes: `Database = IssuesMixin + NotesMixin + MetricsMixin + DatabaseCore`; `Orchestrator = CompletionMixin + LifecycleMixin + OrchestratorCore`
- CAS (compare-and-swap) for all status transitions: `try_transition_issue_status(id, from_status, to_status, expected_assignee)`
- Agents are ephemeral: created per task, deleted after merge. Not persistent identity.
- `agent_id` is a correlation key — FK constraints dropped with `PRAGMA foreign_keys = OFF` before deleting agents
- Async wrappers via `run_in_executor` for blocking git ops (see `git.py:_async_wrapper`)
- Config fields stored as UPPERCASE on the `_Config` object (e.g., `Config.MAX_AGENTS`, `Config.BACKEND`)
- Module-level re-exports in `__init__.py` for test mocking: `import hive.orchestrator as _mod` then access `_mod.Config` to allow `patch.object`
- Worker communication via JSONL files in worktree: `.hive-result.jsonl` (completion), `.hive-notes.jsonl` (discoveries)
- Prompt templates in `src/hive/prompts/*.md`, loaded and cached via `string.Template`
- snake_case everywhere, absolute imports preferred

## Dependencies & Integration
- **aiohttp**: HTTP client for Claude WebSocket backend REST calls
- **typer + rich**: CLI framework and terminal formatting
- **pyyaml**: YAML parsing for backend responses
- **SQLite**: Embedded database at `~/.hive/hive.db` (no ORM, raw `sqlite3`)
- **Claude CLI**: Workers/refinery run as Claude CLI sessions via WebSocket (`--sdk-url ws://`)
- **Codex CLI**: Alternative backend via `codex app-server --listen stdio://` (JSON-RPC)
- **Git**: Workers operate in git worktrees under `<project>/.worktrees/`; merges are ff-only to main
- **datasette**: Optional UI dependency for browsing the DB

## Gotchas
- DB at `~/.hive/hive.db`, NOT in project root. Shared across all projects.
- `Config.load_global()` must be called before accessing any config fields — CLI does this automatically, tests use an autouse fixture.
- Orchestrator `start()` must start SSE/WS task BEFORE `merge_processor.initialize()` — refinery creates a session eagerly.
- Claude WS handshake: WS connect → server sends `user` message → CLI responds with `system/init`. Don't send `system/init` first.
- macOS provenance: daemon inherits `com.apple.provenance` from Claude Code sandbox. Fix: `xattr -rc .git` from non-sandboxed terminal.
- `uv tool install --force` reuses cached wheel — use `--force --reinstall` to rebuild from source. Always restart daemon after reinstall.
- Worktree creation retries with backoff — concurrent creation can fail with "invalid reference: main" due to git ref contention.
- `PRAGMA foreign_keys = OFF` required before deleting agent rows (existing DBs have FK constraints baked in).
- Daemon accumulation: `hive start` scans process table and kills orphans, but stale PID files can still cause confusion.
