# Orchestrator Refactor Plan

_Concrete plan to reduce orchestration complexity and linecount without weakening the race-safety guarantees already covered by tests._

**Created**: 2026-03-14
**Scope**: `src/hive/orchestrator/*.py`, with light DB support changes only where they clearly simplify orchestration

---

## Why This Refactor

The orchestration code is not complex because the business rules are unusual. It is complex because the same concerns are represented in too many places:

- transition decisions
- ownership fencing
- cleanup and teardown
- audit logging
- retry/anomaly accounting

The highest-cost functions today are:

- `LifecycleMixin.monitor_agent()` in `src/hive/orchestrator/lifecycle.py`
  - `pycfg`: 55 blocks, 19 branches, cyclomatic complexity 22
- `CompletionMixin.handle_agent_complete()` in `src/hive/orchestrator/completion.py`
  - `pycfg`: 38 blocks, 11 branches, cyclomatic complexity 17
- `CompletionMixin._handle_agent_failure()` in `src/hive/orchestrator/completion.py`
  - `pycfg`: complexity 7
- `OrchestratorCore.main_loop()` in `src/hive/orchestrator/core.py`
  - `pycfg`: complexity 7
- `LifecycleMixin._spawn_worker_inner()` in `src/hive/orchestrator/lifecycle.py`
  - `pycfg`: complexity 6
- `LifecycleMixin._handle_stalled_with_session_check()` in `src/hive/orchestrator/lifecycle.py`
  - `pycfg`: complexity 6

The tests strongly suggest the real system contract is:

- DB transitions must remain CAS-safe
- duplicate handlers must be harmless
- stale agents/sessions must reconcile cleanly
- cleanup must happen on every terminal path

The plan below preserves those constraints while simplifying the orchestration surface.

---

## Goals

1. Shrink the biggest orchestration functions by moving decisions into small, explicit types.
2. Put cleanup policy in one place instead of re-deciding it in spawn, completion, cancel, stall, shutdown, and reconciliation code.
3. Keep the current test guarantees intact while making the state machine easier to read and extend.
4. Reduce linecount as a side effect of better structure, not by clever compression.

## Non-Goals

1. Do not redesign the merge processor in this pass.
2. Do not rewrite the DB layer away from CAS transitions.
3. Do not combine everything into one “framework” abstraction.
4. Do not start with a schema migration unless the earlier phases prove it is worth it.

---

## Design Principles

### 1. Decision First, Effects Second

Handlers should first compute a compact plan, then execute it.

Today, most hot paths interleave:

- branch logic
- DB transitions
- backend session cleanup
- worktree cleanup
- event logging

That makes the code hard to reason about and hard to test in smaller pieces.

### 2. One Owner for Terminalization

The code currently splits agent terminalization across:

- `_try_claim_agent_for_handling`
- `_mark_agent_failed`
- `_delete_agent_row`
- `_teardown_agent`

Those should become one coherent surface with a small number of legal actions.

### 3. One Issue Disposition Engine

Retry, agent-switch, escalate, and terminal-skip decisions should be expressed once and reused by:

- completion failure
- stalled-agent handling
- startup reconciliation

### 4. Keep the DB as the Safety Boundary

The DB CAS transitions are correct and should stay the concurrency boundary. The refactor should simplify the Python above that boundary, not move correctness out of the DB.

---

## Target Shape

### Proposed Orchestrator Submodules

The current split into `core.py`, `lifecycle.py`, and `completion.py` is directionally good, but the hot functions still carry too many responsibilities. The refactor should move toward this structure:

- `orchestrator/monitoring.py`
  - monitor loop signal types
  - timeout tick handling
  - idle/stall verification helpers
- `orchestrator/disposition.py`
  - issue outcome and escalation decisions
  - shared retry / switch / escalate logic
- `orchestrator/terminalization.py`
  - handler claim fence
  - cleanup planning
  - session/worktree/in-memory teardown execution
- `orchestrator/completion.py`
  - completion-specific decision logic only
- `orchestrator/core.py`
  - startup, loop scheduling, wiring

This does not have to be done in one shot. The phases below keep the code working at each step.

### Core Types To Introduce

These should be small dataclasses and enums, not framework objects.

```python
class MonitorSignal(Enum):
    FILE_RESULT = "file_result"
    IDLE_HINT = "idle_hint"
    CANCELED = "canceled"
    STALL_CONTINUE = "stall_continue"
    STALL_STOP = "stall_stop"


class IssueDisposition(Enum):
    RETRY = "retry"
    AGENT_SWITCH = "agent_switch"
    ESCALATE = "escalate"
    ANOMALY_ESCALATE = "anomaly_escalate"
    TERMINAL_SKIP = "terminal_skip"
    SUCCESS = "success"


@dataclass
class CleanupPlan:
    cleanup_session: bool = True
    unregister_agent: bool = True
    mark_agent_failed: bool = True
    remove_worktree: bool = False


@dataclass
class CompletionPlan:
    disposition: IssueDisposition
    cleanup: CleanupPlan
    events: list[tuple[str, dict]]
    enqueue_merge: bool = False
```

The important point is not the exact names. The important point is to make “what happens next” explicit and compact.

---

## Recommended Refactor Order

## Phase 1: Split `monitor_agent()` Into Signal Helpers

### Why First

`monitor_agent()` is the single worst hotspot by structure and by readability. It mixes:

- result-file truth
- SSE hints
- timeout behavior
- cancellation
- stall detection
- final cleanup

That is the best place to win immediate clarity.

### Changes

Extract helpers with narrow responsibilities:

- `_read_monitor_completion_truth(agent) -> Optional[dict]`
- `_wait_for_monitor_signal(agent, event, timeout) -> MonitorSignal | TimeoutSentinel`
- `_handle_monitor_timeout(agent, session_id) -> MonitorSignal`
- `_finalize_monitor_exit(agent, session_id, file_result, signal)`

Then reduce `monitor_agent()` to a short loop:

1. check result-file truth
2. wait for next signal
3. normalize timeout/idle/cancel/stall into a `MonitorSignal`
4. route on the signal

### Constraints

- preserve the snapshotted `session_id` behavior already covered by race tests
- do not change the external event semantics

### Expected Outcome

- `monitor_agent()` becomes a dispatcher instead of a policy blob
- linecount drops in `lifecycle.py`
- timeout/stall logic becomes directly unit-testable

---

## Phase 2: Centralize Agent Terminalization

### Problem

Agent terminalization currently exists in overlapping forms:

- early spawn-orphan deletion
- completion teardown
- stall teardown
- cancel teardown
- shutdown teardown

This creates a lot of repeated “should I unregister, fail, delete, or clean up?” logic.

### Changes

Introduce a focused helper or collaborator, for example:

```python
class AgentTerminalizer:
    def claim_handler(self, agent, handler_name) -> bool: ...
    def delete_spawn_orphan(self, agent_id) -> None: ...
    async def execute(self, agent, plan: CleanupPlan) -> None: ...
```

Move these responsibilities behind it:

- ownership fencing
- backend session cleanup
- in-memory map cleanup
- DB terminalization
- optional worktree cleanup

### Immediate Wins

- `_spawn_worker_inner()` loses a lot of bespoke cleanup code
- `cancel_agent_for_issue()`, `handle_stalled_agent()`, and `handle_agent_complete()` stop directly stitching cleanup together
- race semantics remain centralized instead of implicit

---

## Phase 3: Introduce Shared Issue Disposition

### Problem

The same retry ladder is expressed in different ways across:

- `_handle_agent_failure()`
- `handle_stalled_agent()`
- stale-agent reconciliation in `core.py`

That duplication is the main reason the state machine feels larger than it is.

### Changes

Create a small shared analyzer:

```python
@dataclass
class DispositionContext:
    issue_id: str
    agent_id: str | None
    failure_reason: str | None
    model: str | None


@dataclass
class DispositionDecision:
    disposition: IssueDisposition
    target_status: str | None
    event_type: str | None
    detail: dict
```

Add one method that only decides:

- anomaly escalate
- retry
- agent switch
- escalate

and a second method that applies the decision through existing DB CAS helpers.

### Important Rule

The decision function should not mutate state. It should only inspect current counters and produce a `DispositionDecision`.

### Expected Outcome

- `_handle_agent_failure()` becomes very short
- reconciliation can reuse the same ladder instead of custom branching
- the actual issue state machine becomes visible in one place

---

## Phase 4: Turn Completion Handling Into `CompletionPlan -> execute`

### Problem

`handle_agent_complete()` currently does five different jobs:

1. ownership claim
2. result/notes cleanup
3. completion transition choice
4. success/failure side effects
5. teardown

### Changes

Keep the existing `CompletionDecision`, but push it further:

- `harvest_notes(agent) -> NotesSummary`
- `_decide_completion_transition(...) -> CompletionDecision`
- `_build_completion_plan(agent, decision, file_result) -> CompletionPlan`
- `_execute_completion_plan(agent, plan) -> None`

That separates:

- decision logic
- merge enqueue logic
- logging
- final cleanup choice

### Expected Outcome

- the large `match` in `handle_agent_complete()` becomes smaller
- success and failure flows stop being interleaved
- the completion path becomes much easier to review

---

## Phase 5: Simplify Spawn Flow Around an Explicit Spawn Plan

### Problem

`_spawn_worker_inner()` is not as structurally complex as the monitor/completion code, but it still mixes:

- project resolution
- worktree creation
- issue claim
- backend session creation
- DB updates
- active-agent registration
- failure cleanup

### Changes

Extract a small linear plan:

- `prepare_spawn(issue) -> SpawnContext`
- `create_spawn_resources(ctx) -> SpawnResources`
- `activate_spawn(ctx, resources) -> AgentIdentity`
- `cleanup_failed_spawn(ctx, resources, exc) -> None`

This should reduce the number of nested failure branches without changing semantics.

### Expected Outcome

- smaller spawn function
- easier to audit the “what has been allocated so far?” logic
- less bespoke cleanup

---

## Phase 6: Optional Execution-State Table

### Only Do This If Needed

This is the only step that likely needs a schema change. It is optional.

### Problem

Retry/switch/anomaly routing currently queries the event log every time. That is simple from an audit perspective, but it scatters runtime state across metrics queries.

### Option

Introduce an `issue_execution_state` table or equivalent columns with:

- `retry_count`
- `agent_switch_count`
- `recent_failure_count`
- `last_reset_event_id`

Events still remain the audit log, but the runtime state becomes cheaper and clearer to consume.

### Recommendation

Do not start here. First see how much simplification Phases 1-5 achieve without a schema migration.

---

## Proposed File-by-File Impact

### `src/hive/orchestrator/lifecycle.py`

Refactor heavily.

- shrink `monitor_agent()`
- shrink `_spawn_worker_inner()`
- keep lifecycle-specific orchestration entry points

### `src/hive/orchestrator/completion.py`

Refactor heavily.

- retain completion-specific decision logic
- move generic failure disposition logic out if it becomes shared

### `src/hive/orchestrator/core.py`

Refactor moderately.

- keep startup and scheduler wiring
- reduce custom reconciliation branching by reusing shared disposition/terminalization helpers

### `src/hive/db/issues.py`

Little or no behavior change expected.

- continue to own CAS issue transitions
- possibly add a tiny helper only if it removes repeated orchestration code cleanly

### `src/hive/db/core.py`

Little or moderate behavior change expected.

- likely no semantic changes beyond supporting clearer terminalization helpers

---

## Testing Strategy

Do not refactor first and “fix tests later.” Use the current test suite as a safety fence and add focused tests only where the new seams justify them.

### Must-Stay-Green Coverage

- `tests/test_race_conditions.py`
- `tests/test_orchestrator.py`
- `tests/test_integration.py`

### Add Small Unit Tests Around New Seams

For example:

- `MonitorSignal` generation from timeout / idle / cancel conditions
- `DispositionDecision` ladder behavior for retry / switch / escalate / anomaly cases
- `AgentTerminalizer.execute()` matrix for session/worktree/in-memory cleanup choices

The goal is to shift some testing burden from giant end-to-end paths to smaller decision units.

---

## Rollout Plan

1. Refactor `monitor_agent()` first.
2. Introduce centralized terminalization.
3. Introduce shared issue disposition.
4. Simplify completion handling around `CompletionPlan`.
5. Simplify spawn flow.
6. Reassess whether a schema-backed execution-state table is still necessary.

Each step should land independently and keep the suite green.

---

## Recommended First Patch Series

If this work is implemented incrementally, the first 3 PRs or commits should be:

1. `monitor_agent` extraction only
   - no behavior changes
   - new signal enum and helpers

2. terminalization centralization
   - route cancel / stall / completion teardown through one executor

3. shared issue disposition
   - unify retry / agent-switch / escalate decisions

That sequence should give the biggest readability win with the lowest migration risk.

---

## Success Criteria

The refactor is successful if:

1. `monitor_agent()` and `handle_agent_complete()` are each small enough to read in one pass.
2. there is exactly one obvious place to understand retry/escalation decisions.
3. there is exactly one obvious place to understand agent teardown policy.
4. race-condition tests remain green.
5. total orchestrator linecount drops without hiding behavior behind vague abstractions.

---

## Anti-Patterns To Avoid

1. Do not replace explicit logic with a generic state-machine framework.
2. Do not fold DB correctness into Python-only helpers.
3. Do not introduce inheritance-heavy “manager” classes.
4. Do not mix schema changes into the first simplification pass.
5. Do not optimize for fewer files if it makes responsibilities less clear.

The target is boring, explicit, testable code.
