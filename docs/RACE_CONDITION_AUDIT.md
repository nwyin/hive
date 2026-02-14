# Race Condition & Concurrency Audit

**Date**: 2026-02-14
**Scope**: Full sweep of all async/concurrent code paths in Hive orchestrator

---

## Summary

Hive uses a single-threaded asyncio event loop, which eliminates traditional threading races but introduces a different class of bugs: **interleaving across await points**. Two async tasks can't run simultaneously, but they *can* interleave at every `await`, meaning any state mutations that span multiple awaits are vulnerable to inconsistency.

The audit found **3 confirmed bugs**, **4 design-level concerns**, and **3 suspect areas** worth testing.

---

## Confirmed Bugs

### BUG-1: `monitor_agent` finally block deletes the *new* session's event after molecule cycling

**Severity**: High
**Files**: `orchestrator.py:832-836`, `orchestrator.py:1110`

**The flow**:
1. `monitor_agent(agent)` detects idle → calls `handle_agent_complete(agent)`
2. `handle_agent_complete` sees this is a molecule step → calls `cycle_agent_to_next_step(agent, next_step)`
3. Inside `cycle_agent_to_next_step`, **agent.session_id is mutated** to the new session's ID (line 1110)
4. A new `asyncio.Event()` is created for the new session (line 1143)
5. A new `monitor_agent(agent)` task is spawned (line 1162)
6. Control returns up the call chain back to the *original* `monitor_agent`
7. The original `monitor_agent`'s `finally` block runs:
   ```python
   if agent.session_id in self.session_status_events:
       del self.session_status_events[agent.session_id]
   ```
8. But `agent.session_id` now points to the **new** session → it **deletes the new session's event**

**Consequence**: The new `monitor_agent` task can never be woken by SSE events. It falls back to polling every 30s, and may false-stall if the lease expires before the next poll catches idle.

**Fix**: Capture `session_id` as a local variable at the start of `monitor_agent`, before any mutations can occur:

```python
async def monitor_agent(self, agent: AgentIdentity):
    # Snapshot the session_id we're monitoring — agent object may be
    # mutated by cycle_agent_to_next_step during molecule processing.
    my_session_id = agent.session_id
    try:
        event = self.session_status_events.get(my_session_id)
        if not event:
            return
        self._session_last_activity[my_session_id] = datetime.now()
        # ... rest of monitoring using my_session_id ...
    finally:
        if my_session_id in self.session_status_events:
            del self.session_status_events[my_session_id]
        self._session_last_activity.pop(my_session_id, None)
```

---

### BUG-2: `time.sleep()` in `create_worktree` blocks the entire event loop

**Severity**: High
**File**: `git.py:73`

```python
time.sleep(1.0 * (attempt + 1))  # blocks event loop for 1-3 seconds!
```

This is a synchronous sleep inside a function called from `spawn_worker` (which runs on the event loop). During these 1–3 seconds, **nothing else runs**: no SSE events processed, no permissions resolved, no workers monitored, no stall checks.

Multiple workers spawning concurrently (e.g., 3 issues become ready at once) could cascade: worker 1 retries with 1s sleep, worker 2 retries with 2s sleep — that's 3+ seconds of total event loop blockage.

**Fix**: Run worktree creation in a thread executor, or make the retry async:

```python
# Option A: Run the entire blocking function in executor
worktree_path = await asyncio.get_event_loop().run_in_executor(
    None, create_worktree, str(self.project_path), agent_name
)

# Option B: Make create_worktree async-aware (less invasive)
# In git.py, add an async wrapper:
async def create_worktree_async(project_path: str, agent_name: str, ...) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, create_worktree, project_path, agent_name, ...
    )
```

---

### BUG-3: Orphaned agent record when worktree creation fails

**Severity**: Medium
**File**: `orchestrator.py:592-608`

```python
agent_id = self.db.create_agent(name=agent_name, ...)  # Agent created in DB

try:
    worktree_path = create_worktree(...)
except Exception as e:
    self.db.log_event(issue_id, agent_id, "worktree_error", ...)
    return  # ← Agent left in 'idle' status in DB, never cleaned up
```

Every worktree creation failure leaves a phantom agent in the DB. Over time, this pollutes the agents table with records that will never be used.

**Fix**: Mark the agent as failed on worktree error:

```python
except Exception as e:
    self.db.log_event(issue_id, agent_id, "worktree_error", {"error": str(e)})
    # Clean up the orphaned agent record
    self.db.conn.execute(
        "UPDATE agents SET status = 'failed' WHERE id = ?",
        (agent_id,),
    )
    self.db.conn.commit()
    return
```

---

## Design Concerns

### DC-1: Blocking subprocess calls throughout the merge pipeline

**Severity**: High (under load)
**Files**: `merge.py:169-205`, `git.py:127-168`, `git.py:196-232`, `git.py:235-263`

All git operations and test commands use synchronous `subprocess.run()`:
- `rebase_onto_main()` — could take seconds for large repos
- `run_command_in_worktree(cmd, timeout=300)` — up to **5 minutes** blocking
- `merge_to_main()` — checkout + merge
- `remove_worktree()` — force removal + prune

While these run on the merge processor (which is sequential anyway), they share the same event loop as the orchestrator. During a 5-minute test run, **no other async work happens**: SSE events queue up, permissions go unresolved, stall checks don't run, new workers can't spawn.

**Fix**: Wrap all subprocess-heavy operations in `run_in_executor`:

```python
async def _try_mechanical_merge(self, entry):
    loop = asyncio.get_event_loop()

    rebase_ok = await loop.run_in_executor(
        None, rebase_onto_main, worktree
    )
    if not rebase_ok:
        await loop.run_in_executor(None, abort_rebase, worktree)
        return (False, None)

    if Config.TEST_COMMAND:
        test_ok, test_output = await loop.run_in_executor(
            None, run_command_in_worktree, worktree, Config.TEST_COMMAND
        )
        # ...
```

---

### DC-2: Double-handling race between SSE error handler and monitor_agent

**Severity**: Medium
**Files**: `orchestrator.py:106-117`, `orchestrator.py:761-836`, `orchestrator.py:1175-1235`

When a session error SSE event arrives:
1. `handle_session_error` calls `handle_stalled_agent(agent)` directly
2. `handle_stalled_agent` awaits `_cleanup_session(agent)` — **yields to event loop**
3. During this yield, `monitor_agent` (a separate task) could time out and also try to process the agent
4. Both tasks pass their guards (`if agent.agent_id not in self.active_agents`) because neither has unregistered the agent yet

The guard at `handle_agent_complete:861` and `handle_stalled_agent:1187` check membership in `active_agents`, but the agent isn't removed until the end of each function. Between the guard check and the unregistration, both code paths can execute concurrently (interleaved across awaits).

**Potential consequences**:
- Double session cleanup (benign — swallowed exceptions)
- Double issue status update (could set `done` then `open`, or `done` then `failed`)
- Duplicate merge queue entries

**Fix**: Use an "in-progress" set to atomically claim ownership:

```python
# Add to __init__:
self._handling_agents: set[str] = set()

# In handle_agent_complete / handle_stalled_agent:
if agent.agent_id not in self.active_agents:
    return
if agent.agent_id in self._handling_agents:
    return  # Another handler is already processing this agent
self._handling_agents.add(agent.agent_id)
try:
    # ... do work ...
finally:
    self._handling_agents.discard(agent.agent_id)
```

---

### DC-3: PID file TOCTOU race in daemon startup

**Severity**: Low (requires concurrent CLI invocations)
**File**: `daemon.py:94-127`

```python
existing_pid = self._read_pid()          # Step 1: read
if existing_pid and self._is_running(existing_pid):  # Step 2: check
    return False
# ... gap where another process could also pass the check ...
proc = subprocess.Popen(...)              # Step 3: spawn
self._write_pid(proc.pid)                # Step 4: write
```

Two `hive daemon start` commands in rapid succession could both pass the PID check and spawn two daemons. The second write would overwrite the first PID, making the first daemon unmanageable.

**Fix**: Use `fcntl.flock` on the PID file for advisory locking:

```python
import fcntl

def start(self, db_path: str = "hive.db") -> bool:
    self._ensure_dirs()

    with open(self.pid_file, 'a+') as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False  # Another daemon is starting

        f.seek(0)
        existing_pid = f.read().strip()
        if existing_pid and self._is_running(int(existing_pid)):
            return False

        proc = subprocess.Popen(...)
        f.seek(0)
        f.truncate()
        f.write(str(proc.pid))
        f.flush()
        # Lock released when file is closed
```

---

### DC-4: SSE `stop()` is defeated by `connect()` resetting `self.running = True`

**Severity**: Medium
**File**: `sse.py:85`, `sse.py:140-142`, `sse.py:144-162`

```python
async def connect(self):
    self.running = True  # ← Always resets to True!
    ...

def stop(self):
    self.running = False  # ← Can be undone by connect()

async def connect_with_reconnect(self, ...):
    while self.running and ...:
        try:
            await self.connect()  # ← Resets self.running = True
        except Exception:
            await asyncio.sleep(retry_delay)
```

If `stop()` is called during the `asyncio.sleep(retry_delay)` in the reconnect loop, the next `connect()` call resets `self.running = True`. The SSE client continues running after being stopped.

**Fix**: Don't set `self.running = True` inside `connect()`:

```python
async def connect(self):
    # Remove: self.running = True
    ...

async def connect_with_reconnect(self, ...):
    self.running = True  # Set once at the start
    ...
```

---

## Suspect Areas (Need Testing)

### SA-1: `merge_to_main` checks out main in the user's working tree

**File**: `git.py:196-232`

`merge_to_main` runs `git checkout main` and `git merge --ff-only` in the **main repo directory** (not a worktree). If the user has uncommitted changes or is on a different branch, this will either fail or silently switch their checkout.

This is also a serialization concern: if the merge loop dies and restarts while a `running` merge entry exists, and another merge starts simultaneously, both could try to checkout main and merge different branches.

**Testing needed**: What happens if the user is actively working in the main repo when a merge runs? Is there a lock? Does it corrupt the user's working state?

**Potential fix**: Run merges in a dedicated merge worktree rather than the main repo:

```python
# Create a persistent merge worktree
merge_worktree = project_path / ".worktrees" / "_merge"
# Do all checkout/merge operations there
# Then update main's ref directly
```

---

### SA-2: `get_queued_merges` doesn't filter out `running` entries on restart

**File**: `db.py:656-678`, `merge.py:118-154`

`get_queued_merges` only returns `status = 'queued'` entries. If the merge processor dies mid-merge, the entry stays in `running` status forever. There's no reconciliation for stuck `running` merge entries.

**Testing needed**: Kill the daemon during a merge, restart — does the merge entry stay stuck as `running`? Is there reconciliation?

**Potential fix**: On startup, reset any `running` merge entries back to `queued`:

```python
async def initialize(self):
    # Reset any stuck 'running' merges from a previous crash
    self.db.conn.execute(
        "UPDATE merge_queue SET status = 'queued' WHERE status = 'running'"
    )
    self.db.conn.commit()
    ...
```

---

### SA-3: `claim_issue` CAS window with `spawn_worker` resource pre-allocation

**File**: `orchestrator.py:579-615`

The current flow in `spawn_worker` is:
1. Create agent in DB (resource allocation)
2. Create git worktree (resource allocation)
3. Atomic claim via CAS
4. If claim fails → clean up worktree (but not agent — see BUG-3)

This "allocate then claim" pattern means resources (agent records, worktrees) are created for issues that may not be claimable. Under high contention (many orchestrators or rapid issue creation), this wastes resources.

**Testing needed**: What's the failure rate of claim_issue in practice? If it's near-zero (single orchestrator), this is a non-issue. If multiple orchestrators are planned, this should be restructured to "claim then allocate."

---

## Summary Table

| ID | Type | Severity | Description | Fixable? |
|----|------|----------|-------------|----------|
| BUG-1 | Bug | High | monitor_agent finally block deletes new session's event after molecule cycling | Yes — snapshot session_id |
| BUG-2 | Bug | High | time.sleep() in create_worktree blocks event loop | Yes — use executor |
| BUG-3 | Bug | Medium | Orphaned agent records when worktree creation fails | Yes — mark agent failed |
| DC-1 | Design | High | All subprocess calls block event loop | Yes — use executor |
| DC-2 | Design | Medium | Double-handling race between SSE handler and monitor | Yes — add handling set |
| DC-3 | Design | Low | PID file TOCTOU race | Yes — use flock |
| DC-4 | Design | Medium | SSE stop() defeated by connect() resetting flag | Yes — move flag set |
| SA-1 | Suspect | Medium | merge_to_main modifies user's working tree | Needs testing |
| SA-2 | Suspect | Medium | Stuck 'running' merge entries after crash | Needs testing |
| SA-3 | Suspect | Low | Resource pre-allocation before claim | Needs testing |
