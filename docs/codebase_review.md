# Hive Codebase Review

Three-agent review: bug hunter, complexity analyst, and devil's advocate.

## Bugs Worth Fixing

These survived the devil's advocate's scrutiny.

### Medium Severity

**SSE `sock_read=30` timeout** — `backend_opencode.py:332`

Long-lived SSE connections drop every 30s of idle. Should be `None` or `300`.

**Reconciliation uses `or` instead of `and`** — `orchestrator.py:361`

```python
if retry_count < Config.MAX_RETRIES or agent_switch_count < Config.MAX_AGENT_SWITCHES:
```

Creates a slow retry loop when one budget is exhausted but not the other. Should match `_choose_escalation` logic, which checks retries first, then agent switches sequentially.

**`_send_initialize` sleeps 0.5s instead of awaiting response** — `backend_claude.py:522-538`

Generates a `request_id` but never correlates a response. System prompt could be silently dropped if CLI is slow.

**Duplicate unacked-notes check** — `orchestrator.py:1236-1283`

`_decide_completion_transition` checks `get_required_unacked_deliveries` twice with identical code. Both run synchronously with no yield between them, so the second check is genuinely redundant.

### Low Severity

**`total_changes` misuse in migration** — `db.py:300-302`

`total_changes` is a cumulative counter across the connection lifetime, not per-statement. Should use `cursor.rowcount`.

**`log_fd` leak on Popen failure** — `daemon.py:183-206`

File descriptor opened before `Popen` is never closed if `Popen` raises. Needs try/finally.

**`list_issues` passes LIMIT as string** — `cli.py:154`

`str(limit)` — works via SQLite coercion but wrong by intent.

## Bugs Successfully Defended

**SQL `.format()` for config values** — Values are `int`-coerced from config, never user input. SQLite `datetime()` syntax requires inline intervals. Safe in practice.

**`check_same_thread=False`** — Single asyncio loop, all DB access on main thread. Executor threads only run git subprocesses. Currently safe.

**FK pragma toggle** — Documented in schema comments and the design doc. Agents are ephemeral by design; events/notes keep `agent_id` as a correlation key for analytics. The PRAGMA toggle is scoped and immediately re-enabled.

**PID file management** — `_find_all_daemon_pids()` scans the process table as a safety net, and `_kill_orphaned_daemons()` cleans up processes the PID file doesn't track. Two-layer approach (PID file + process scan) is more robust than PID files alone.

**Permission policy** — Workers are scoped to their worktree. Standard tools get `"once"` (not `"always"`) so each invocation is tracked. Unknown permissions are left unresolved for human review.

## Complexity Worth Addressing

### High

**`orchestrator.py` — 1957-line class with ~30 methods**

The `Orchestrator` manages agent lifecycle, session monitoring, SSE handling, permission policy, degraded mode, budget caps, stall detection, completion assessment, failure escalation, epic cycling, and the merge processor loop. The devil's advocate argues splitting creates cross-references with shared mutable state — fair point, but the 110-line `_decide_completion_transition` with duplicated code and the 150-line `handle_agent_complete` are too dense. Extract `CompletionHandler` and `ReconciliationService` at minimum.

**`cli.py` — 160-line if/elif dispatch, 2140 lines total**

A command registry pattern would clean up the dispatch without abstraction cost. The queen launcher (220+ lines across 4 methods) is a subsystem that belongs in its own module. `status()` at 177 lines queries 7+ data sources and formats output — effectively a dashboard renderer.

### Medium

**`db.py` — `get_token_usage` query duplication** (`db.py:881-1000`)

4 nearly identical SQL queries with the same WHERE clause. A small query builder helper would cut 50+ lines.

**`daemon.py` — tripled backend dispatch** (`daemon.py:300-397`)

Three identical 15-line blocks differing only in backend class. A one-line factory call would do.

**`doctor.py` — duplicated worktree parsing** (`doctor.py:314-449`)

`_fix_inv8_ghost_worktrees()` and `check_inv8_ghost_worktrees()` both contain identical `git worktree list --porcelain` parsing (~30 lines each). Extract `_parse_worktree_list()`.

**`_decide_completion_transition` — duplicated unacked-notes check** (`orchestrator.py:1236-1283`)

Exact same `materialize_issue_deliveries` + `get_required_unacked_deliveries` + event logging + return pattern appears twice. If both checks are intentional, extract a helper. If not, remove the duplicate.

### Low

**`merge.py` — retry-on-death pattern** (`merge.py:199-264`)

Double-nested try/except for "retry once on session death" creates 3 levels of exception handling. A retry decorator or loop would be clearer.

**`prompts.py` — repeated JSON parse pattern** (`prompts.py:49-122`)

Three identical blocks for `incomplete_events`, `merge_rejected_events`, and `stalled_events`. Extract a parse-and-format helper.

**Raw SQL string formatting in orchestrator** — Several places use `.format()` to inject `Config.LEASE_DURATION` or `Config.LEASE_EXTENSION` into SQL. While safe (integer config values), inconsistent with parameterized queries used elsewhere.

## What a New Developer Would Struggle With

1. **Dual SSE/polling completion detection** in `monitor_agent()` — understanding why both exist, what `my_session_id` vs `agent.session_id` means, and the session ID snapshot pattern.
2. **Completion transition state machine** — the enums are great but side-effects are spread across `_decide_completion_transition`, `handle_agent_complete`, `_handle_agent_failure`, and `_choose_escalation`.
3. **Two-gate pattern in `backend_claude.py`** — `ws_connected` vs `connected` and the initialization handshake where the CLI sends the first message.
4. **Which backend interface methods are stubs vs real** — `SSEClient` and `OpenCodeClient` each have ~10 `raise NotImplementedError` stubs for the half of `HiveBackend` they don't implement.

## Strengths

### Architecture

**Atomic CAS claim** (`db.py:417-468`) — Single UPDATE with WHERE guards (`assignee IS NULL` + dependency check) eliminates TOCTOU races on issue claiming. The dependency check is re-evaluated at claim time, not just at queue time.

**4-phase crash recovery** in `_reconcile_stale_agents` (`orchestrator.py:296-437`) — Handles every daemon crash scenario: died mid-spawn, died mid-completion, backend restarted independently. Worktree preservation logic correctly avoids destroying worktrees still needed by the merge queue.

**Escalation state machine** (`orchestrator.py:1472-1560`) — Clean decision chain: anomaly detection -> retry tier -> agent switch tier -> escalate. The anomaly detection (burst failures within a time window) is a circuit breaker preventing wasted tokens on fundamentally broken issues.

**Backend abstraction** (`backends/base.py`) — `HiveBackend` ABC with session management + event streaming as orthogonal capabilities. Claude backend unifies both; OpenCode splits them. Orchestrator accepts both transparently.

### Patterns

**Completion transition table** (`orchestrator.py:1201-1471`) — `_decide_completion_transition()` separates decision from action. `CompletionDecision` dataclass carries all payload. Match/case dispatch with explicit transition table comment makes the state machine auditable.

**`_agent_handling_scope` guard** (`orchestrator.py:1121-1138`) — Async-aware mutex preventing double-handling across await points. `_spawning_issues` set serves the same purpose for spawn TOCTOU races.

**Config source attribution** (`config.py:117-163`) — `get_resolved_config()` walks all 4 layers to determine per-field provenance. Answers "why is MAX_AGENTS 3?" directly.

**Doctor invariant checks** (`doctor.py`) — Registry pattern, per-check isolation, typed `CheckResult` with optional auto-fix callbacks.

### Operational Details

**Process group cleanup** (`backend_claude.py:288-301`) — `os.killpg()` with session leader via `start_new_session=True`. SIGTERM -> wait(5s) -> SIGKILL. Correctly handles grandchild processes.

**`CLAUDECODE` env stripping** (`daemon.py:182`, `backend_claude.py:144`) — Prevents nested Claude session refusal.

**Merge queue dirty main guard** (`merge.py:142-171`) — Checks main worktree dirty state before attempting merge. Dirty snapshot comparison avoids log spam.

**Diagnostic report** (`diag.py`) — `_section()` wrapper catches exceptions per-section so one broken section doesn't prevent the rest. Includes system info, resolved config with sources, daemon status, doctor checks, DB stats, recent events, daemon log tail, and backend reachability.

## Summary

No critical bugs. The architecture makes deliberate, documented tradeoffs (ephemeral agents, dropped FKs, denormalized model) well-suited to the problem domain.

**Fix now**: SSE timeout, reconciliation `or`/`and`, init sleep-vs-await.

**Clean up**: Dedupe the unacked-notes check, extract completion/reconciliation from the orchestrator, registry-ify the CLI dispatch, move the queen launcher to its own module.

**Leave alone**: SQL format for config ints, `check_same_thread`, FK pragma — reasonable tradeoffs with documented rationale.
