# Orchestrator LOC Reduction Plan

_Concrete follow-up plan for reducing orchestrator linecount and local complexity without introducing a framework or blurring race-sensitive behavior._

**Created**: 2026-03-14
**Scope**: `src/hive/orchestrator/*.py`

---

## Goal

The earlier orchestrator cleanup work is done. The next pass is narrower:

- cut LOC where the remaining code is mostly glue
- remove duplicated decision flows
- keep side effects obvious at the call site
- avoid abstractions that only make metrics look better

This is not a redesign. It is a targeted simplification pass.

---

## Current Shape

As of this pass, the main orchestrator files are:

- `src/hive/orchestrator/core.py`: 761 lines
- `src/hive/orchestrator/lifecycle.py`: 737 lines
- `src/hive/orchestrator/completion.py`: 445 lines

The biggest remaining control-flow hotspot is still `LifecycleMixin.monitor_agent()`, but the larger LOC opportunity is now spread across duplicated policy and single-use glue.

Key `pycfg` signals:

- `LifecycleMixin.monitor_agent()`: cyclomatic complexity 17
- `LifecycleMixin._handle_stalled_with_session_check()`: 6
- `LifecycleMixin._handle_monitor_timeout()`: 5
- `CompletionMixin._apply_failure_disposition()`: 7
- reconciliation helpers in `core.py`: individually moderate, collectively glue-heavy

Key `pycg` signals:

- `handle_agent_complete()` is already mostly a dispatcher
- `_spawn_worker_inner()` is already mostly a dispatcher
- reconciliation and lifecycle liveness checks still have duplicated policy encoded across several helpers

---

## Principles

1. Prefer deleting glue over adding helpers.
2. Keep top-level flows readable in one pass.
3. Duplicate 2-3 obvious lines rather than invent a reusable layer for them.
4. Centralize only real shared policy:
   - liveness/completion probing
   - escalation state reads
   - stale-agent reconciliation behavior
5. Do not introduce a generic state machine or planner layer.

---

## Best Next Cuts

## Phase 7: Unify Agent Liveness Probing

### Problem

`lifecycle.py` still has the same liveness policy expressed in multiple places:

- `monitor_agent()`
- `_handle_monitor_timeout()`
- `_handle_stalled_with_session_check()`

They all perform variants of:

1. check result file
2. inspect backend session status
3. decide completion vs continue vs stalled handling

This is the clearest remaining duplicated decision flow in the orchestrator.

### Change

Introduce one narrow helper for the shared probe, for example:

- `_probe_agent_liveness(agent, *, source, session_id_override=None)`

That helper should answer only:

- completion detected from result file
- completion hinted by idle session
- heartbeat/session still healthy
- stalled/error/not_found

`monitor_agent()` and `_handle_stalled_with_session_check()` should both use it, while keeping their different logging and call-site behavior explicit.

### Expected Win

- delete duplicated result-file/session-status logic
- reduce drift between monitor timeout and stale-agent checks
- likely remove 30-50 LOC

---

## Phase 8: Collapse Stale-Agent Reconciliation Glue

### Problem

Startup reconciliation in `core.py` is spread across:

- `_reconcile_handle_session()`
- `_reconcile_handle_issue()`
- `_reconcile_handle_worktree()`
- `_reconcile_process_stale_agents()`

These helpers are mostly single-call glue. They make the reader jump around for one startup-only path.

### Change

Collapse them into one explicit per-agent routine, for example:

- `_reconcile_stale_agent(agent, live_session_ids)`

Keep the control flow linear:

1. session cleanup / ghost decision
2. mark agent failed
3. release or escalate issue
4. preserve or delete worktree

### Expected Win

- remove several one-caller helpers
- keep reconciliation logic in one read path
- likely remove 40-70 LOC

---

## Phase 9: Simplify Completion Failure Shape

### Problem

`handle_agent_complete()` is much better than before, but it still carries more completion-transition ceremony than necessary.

Today, the failure side is split into multiple transition variants that mostly share the same path:

- budget failure
- assessment failure
- validation failure

### Change

Reduce completion handling to three top-level branches:

- skip
- failure
- success

Keep special event logging, but move it into the decision payload or the failure helper so the top-level match does not enumerate multiple equivalent failure branches.

### Expected Win

- smaller transition enum surface
- less dataclass payload ceremony
- likely remove 20-40 LOC

---

## Phase 10: Add a Tiny Escalation Snapshot Helper

### Problem

`completion.py` still recomputes the same escalation inputs several times:

- retry count
- agent switch count
- anomaly failure count

This is not a major complexity problem, but it is repetitive and makes the escalation ladder noisier than it needs to be.

### Change

Add one small helper or dataclass, for example:

- `_read_escalation_state(issue_id)`

or

- `EscalationState(retry_count, agent_switch_count, recent_failures)`

This should only cache the current counts for one decision/application cycle. It should not become a new framework object.

### Expected Win

- fewer repeated DB calls
- less repeated detail assembly
- likely remove 15-30 LOC

---

## Phase 11: Trim Dead or Thin Wrappers

### Problem

Some helpers now look thinner than they are worth:

- `_release_issue()` appears unused
- `_cleanup_session()` is a small one-hop wrapper
- `_teardown_agent()` may now be too thin to justify itself

### Change

Delete dead code first. Then evaluate whether the remaining one-hop wrappers improve call-site readability enough to keep.

### Expected Win

- small LOC reduction
- slightly less indirection in `core.py`

---

## Recommended Order

If the goal is maximum simplification with low risk:

1. Unify liveness probing in `lifecycle.py`
2. Collapse stale-agent reconciliation in `core.py`
3. Simplify completion failure shape in `completion.py`
4. Add a tiny escalation snapshot helper
5. Trim dead or thin wrappers

This order prioritizes removing duplicated policy before removing small helpers.

---

## Anti-Goals

Do not do these in the next pass:

1. Do not introduce a generic orchestration framework.
2. Do not do a schema migration just to reduce LOC.
3. Do not split files further unless it clearly removes duplicated policy.
4. Do not refactor only to improve complexity metrics.
5. Do not hide DB transition behavior behind vague abstractions.

---

## Success Criteria

This pass is successful if:

1. `core.py` and `lifecycle.py` both lose meaningful linecount.
2. the remaining duplicated liveness/reconciliation policy is expressed once.
3. top-level handlers still read as explicit control flow, not as indirection through plans or managers.
4. race-condition and orchestrator tests stay green.
5. the code is shorter because it is simpler, not because it is compressed.
