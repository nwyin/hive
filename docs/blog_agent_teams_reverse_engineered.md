# Reverse-Engineering Claude Code Agent Teams: Architecture, Protocol, and Comparison with Hive

*A rigorous technical analysis of Anthropic's experimental multi-agent coordination system.*

---

## Introduction

Claude Code (v2.1.47) ships with an experimental feature called **Agent Teams** — a system that lets multiple Claude Code sessions coordinate on shared work through a lead-and-teammates topology. Since I've been building [Hive](https://github.com/your/hive), a multi-agent coding orchestrator with a similar goal but very different architecture, I wanted to understand exactly how Anthropic's approach works under the hood.

This post documents what I found through:

1. Reading the [official documentation](https://code.claude.com/docs/en/agent-teams)
2. Examining actual artifacts left on disk by previous team sessions
3. Analyzing the Claude Code binary (v2.1.47) for implementation details
4. Comparing the architecture to Hive's SQLite + orchestrator approach

Every claim is cited to a specific source.

---

## Table of Contents

- [1. Architecture Overview](#1-architecture-overview)
- [2. The Shared Task List](#2-the-shared-task-list)
- [3. Inter-Agent Communication](#3-inter-agent-communication)
- [4. Agent Spawning and Lifecycle](#4-agent-spawning-and-lifecycle)
- [5. Quality Gates and Hooks](#5-quality-gates-and-hooks)
- [6. Token Economics](#6-token-economics)
- [7. Architectural Comparison: Agent Teams vs Hive](#7-architectural-comparison-agent-teams-vs-hive)
- [8. Benchmarking Ideas](#8-benchmarking-ideas)
- [9. Conclusions](#9-conclusions)
- [Sources](#sources)

---

## 1. Architecture Overview

An agent team consists of four components [^docs-arch]:

| Component     | Role                                                                 |
| :------------ | :------------------------------------------------------------------- |
| **Team lead** | The main Claude Code session that creates the team, spawns teammates |
| **Teammates** | Separate Claude Code instances, each with its own context window     |
| **Task list** | Shared work items stored as individual JSON files on disk            |
| **Mailbox**   | Per-agent inbox files for message delivery                           |

The entire coordination layer is **file-based**. No daemon process, no database, no IPC sockets. The filesystem at `~/.claude/` is the sole coordination substrate [^docs-arch]:

```
~/.claude/
├── teams/{team-name}/
│   ├── config.json                  # team membership registry
│   └── inboxes/{agent-name}.json    # per-agent mailbox
└── tasks/{team-name}/
    ├── .lock                        # flock() for concurrent task claiming
    ├── .highwatermark               # auto-increment counter
    ├── 1.json                       # individual task files
    ├── 2.json
    └── ...
```

This is a fundamentally **decentralized** design. The lead is just another Claude session with extra tools (`TeamCreate`, `TeamDelete`, `SendMessage`). There is no background process — coordination emerges from shared file access.

### Team Config

The team config at `~/.claude/teams/{team-name}/config.json` contains a `members` array that teammates read to discover each other [^docs-arch]:

```json
{
  "members": [
    { "name": "team-lead", "agentId": "abc-123", "agentType": "leader" },
    { "name": "researcher", "agentId": "def-456", "agentType": "general-purpose" }
  ]
}
```

Names — not UUIDs — are the primary addressing mechanism. All messaging and task assignment uses the `name` field [^docs-arch].

### A Note on Team Naming

On disk, team directories are identified by UUID, not human-readable names. For example, a real team directory on my machine:

```
~/.claude/teams/b18e107a-fc7a-414d-811c-9466dbdf1c3f/
```

[^disk-teams]: Observed at `/Users/tau/.claude/teams/b18e107a-fc7a-414d-811c-9466dbdf1c3f/`

---

## 2. The Shared Task List

### File Format

Each task is stored as an individual JSON file in `~/.claude/tasks/{team-name}/`. Here's a real example from a previous session [^disk-task-example]:

```json
{
  "id": "1",
  "subject": "Add claude-ws config values to config.py",
  "description": "Add backend, claude_ws_host, claude_ws_port, claude_ws_max_concurrent config fields",
  "activeForm": "Adding config values",
  "status": "completed",
  "blocks": [],
  "blockedBy": []
}
```

**Task schema** [^docs-task-fields]:

| Field         | Type       | Description                                                |
| :------------ | :--------- | :--------------------------------------------------------- |
| `id`          | string     | Numeric ID, auto-incremented via `.highwatermark`          |
| `subject`     | string     | Imperative-form title (e.g., "Run tests")                  |
| `description` | string     | Detailed requirements and acceptance criteria              |
| `activeForm`  | string     | Present-continuous form for spinner display ("Running tests") |
| `status`      | string     | `pending` → `in_progress` → `completed` (or `deleted`)    |
| `blocks`      | string[]   | Task IDs that this task blocks                             |
| `blockedBy`   | string[]   | Task IDs that must complete before this task can start     |

### Concurrency Control

Two special files provide coordination [^disk-lock-hwm]:

- **`.lock`** — A 0-byte file used for filesystem-level mutual exclusion (`flock()`). Present in all 42 task directories observed on my machine.
- **`.highwatermark`** — Contains a single integer (e.g., `"3"`, `"13"`). Represents the next available task ID for auto-incrementing.

### Task Claiming

Task claiming uses file locking to prevent race conditions [^docs-claiming]. Teammates prefer lowest-ID-first ordering [^docs-task-order]. A task with a non-empty `blockedBy` array cannot be claimed until all blocking tasks are in a terminal state.

### Observation: Most Task Directories Are Empty

Of 42 task directories on my machine, only 5 contained actual task JSON files [^disk-task-dirs]. The remaining 37 had only `.lock` and `.highwatermark`. This likely means tasks are cleaned up after completion, or these were sessions where Claude used the internal task list (available since the task list feature launch [^docs-interactive]) without decomposing into subtask files.

---

## 3. Inter-Agent Communication

### Mailbox Pattern

Each agent has a JSON array file at `~/.claude/teams/{team-name}/inboxes/{agent-name}.json`. Here's a real inbox from a previous session where a team-lead dispatched work to a `cp-agent` [^disk-inbox]:

```json
[
  {
    "from": "team-lead",
    "text": "{\"type\":\"task_assignment\",\"taskId\":\"1\",\"subject\":\"Phase 2: Control-plane - remove participants/presence\",\"description\":\"Remove multiplayer code from the control-plane package...\",\"assignedBy\":\"team-lead\",\"timestamp\":\"2026-02-18T02:37:16.890Z\"}",
    "timestamp": "2026-02-18T02:37:16.890Z",
    "read": false
  }
]
```

Note the **JSON-in-JSON** encoding: the `text` field is a JSON string containing a serialized message object. The outer envelope has `from`, `text`, `timestamp`, and `read` fields.

### Message Types

The `type` field inside the `text` payload supports [^docs-messaging] [^binary-analysis]:

| Type                      | Direction        | Purpose                              |
| :------------------------ | :--------------- | :----------------------------------- |
| `task_assignment`         | lead → teammate  | Assign a task with full details      |
| `message`                 | any → any        | Direct message to one recipient      |
| `broadcast`               | lead → all       | Same message to every teammate       |
| `shutdown_request`        | lead → teammate  | Request graceful shutdown            |
| `shutdown_response`       | teammate → lead  | Approve or reject shutdown           |
| `plan_approval_request`   | teammate → lead  | Submit plan for review               |
| `plan_approval_response`  | lead → teammate  | Approve or reject with feedback      |
| `idle_notification`       | teammate → lead  | Auto-sent when teammate's turn ends  |

### Delivery Mechanism

**Write path**: The sender appends a new entry to the recipient's inbox JSON array file.

**Read path**: The recipient polls their own inbox file. New messages are injected as synthetic conversation turns — they appear as if a user sent them [^docs-auto-delivery].

**Broadcast**: Literally writes the same message to every teammate's inbox file. Token cost scales linearly with team size [^docs-broadcast-cost].

There is no WebSocket, no pub/sub, no socket — just file append + file read. The latency between send and receive depends on the recipient's poll interval.

### Peer DM Visibility

When a teammate sends a DM to another teammate, a brief summary is included in the lead's idle notification. This gives the lead visibility into peer collaboration without the full message content [^docs-peer-dm].

---

## 4. Agent Spawning and Lifecycle

### How Teammates Are Created

Each teammate is a **separate `claude` CLI process** [^docs-arch]. The lead spawns them via the `Task` tool with `team_name` and `name` parameters. Environment variables are set on the spawned process [^docs-env-vars]:

- `CLAUDE_CODE_TEAM_NAME` — auto-set on spawned teammates
- `CLAUDE_CODE_PLAN_MODE_REQUIRED` — set to `true` if plan approval is required

### Context Initialization

Teammates load the same project context as any fresh session [^docs-context]:

- `CLAUDE.md` files from the working directory
- MCP servers
- Skills
- The spawn prompt from the lead

**The lead's conversation history does NOT carry over.** Each teammate starts fresh with only the spawn prompt as context.

### Internal Implementation

From binary analysis of Claude Code v2.1.47, the teammate context is managed via `AsyncLocalStorage` with these fields [^binary-analysis]:

- `agentId`, `agentName`, `teamName`
- `parentSessionId`, `color`
- `planModeRequired`

Key internal functions:
- `isTeammate()` / `isTeamLead()` — role detection
- `waitForTeammatesToBecomeIdle()` — synchronization primitive for the lead
- `getTeammateContext()` / `setDynamicTeamContext()` — runtime context management

### Idle Detection

After every LLM turn, a teammate automatically goes idle and sends an `idle_notification` to the lead [^docs-idle]. This is the normal resting state — not an error. Sending a message to an idle teammate wakes it (the next poll cycle picks up the inbox message).

### Shutdown Protocol

1. Lead sends `shutdown_request` to a teammate [^docs-shutdown]
2. Teammate can approve (exits gracefully) or reject (continues working with an explanation)
3. Team cleanup via `TeamDelete` removes `~/.claude/teams/{team-name}/` and `~/.claude/tasks/{team-name}/`
4. Cleanup fails if any teammates are still active — they must be shut down first [^docs-cleanup]

### Permission Inheritance

Teammates inherit the lead's permission mode at spawn time. If the lead runs `--dangerously-skip-permissions`, all teammates do too [^docs-permissions]. Individual modes can be changed post-spawn but not configured per-teammate at spawn time.

---

## 5. Quality Gates and Hooks

Agent Teams integrates with Claude Code's hook system for quality enforcement [^docs-hooks]:

### TeammateIdle Hook

Fires when a teammate is about to go idle. Exit code 2 sends stderr as feedback and prevents idle — the teammate continues working.

```json
{
  "hook_event_name": "TeammateIdle",
  "teammate_name": "researcher",
  "team_name": "my-project"
}
```

### TaskCompleted Hook

Fires when a task is being marked complete. Exit code 2 prevents completion and feeds stderr back as feedback.

```json
{
  "hook_event_name": "TaskCompleted",
  "task_id": "task-001",
  "task_subject": "Implement user authentication",
  "task_description": "Add login and signup endpoints",
  "teammate_name": "implementer",
  "team_name": "my-project"
}
```

This fires in two situations [^docs-task-hook]: (1) when any agent explicitly marks a task completed via `TaskUpdate`, or (2) when an agent team teammate finishes its turn with in-progress tasks.

### Hook Handler Types

| Type      | Description                                                     |
| :-------- | :-------------------------------------------------------------- |
| `command` | Shell script. JSON on stdin, exit codes for decisions.          |
| `prompt`  | Single-turn LLM evaluation. Returns `{ok, reason}`.            |
| `agent`   | Multi-turn subagent with read tools. Up to 50 turns.           |

---

## 6. Token Economics

Agent teams use **approximately 7× more tokens** than standard sessions when teammates run in plan mode [^docs-costs]. Each teammate maintains its own full context window as a separate Claude instance.

### Baseline Reference

- Average Claude Code usage: ~$6/developer/day [^docs-costs]
- Agent teams: roughly proportional to team size on top of baseline

### No Built-In Token Budget

Unlike Hive, Claude Code Agent Teams has **no per-task or per-run token budget enforcement**. The only guidance is "clean up teams when done" and "use Sonnet for teammates to balance cost and capability" [^docs-cost-optimization].

---

## 7. Architectural Comparison: Agent Teams vs Hive

### Side-by-Side

| Dimension                | Claude Code Agent Teams                              | Hive                                                    |
| :----------------------- | :--------------------------------------------------- | :------------------------------------------------------ |
| **Coordination substrate** | Flat files (`~/.claude/tasks/`, `~/.claude/teams/`) | SQLite (`~/.hive/hive.db`)                              |
| **Task format**          | One JSON file per task + `.lock` for claiming        | SQL rows with CAS-style atomic `claim_issue`            |
| **Messaging**            | JSON inbox files (append + poll)                     | SSE/WS event injection + `notes` table in DB            |
| **Agent lifecycle**      | Self-managing CLI processes                          | Orchestrator-managed: spawn, monitor, lease, retry      |
| **Work isolation**       | Shared working directory                             | Per-worker git worktrees                                |
| **Merge strategy**       | None (agents edit files directly)                    | Two-tier: mechanical rebase+test → LLM refinery        |
| **Retry/escalation**     | Manual (lead decides, or user intervenes)            | Automatic: retry → agent-switch → anomaly → escalation  |
| **Topology**             | Lead + flat peers, peer-to-peer messaging            | Central orchestrator + workers + refinery (hub-and-spoke)|
| **Scheduling**           | Self-claim (teammates grab next task)                | Orchestrator polls ready queue, claims atomically        |
| **State durability**     | Files only — no in-process teammate resumption       | SQLite + git — full reconciliation on restart            |
| **Quality gates**        | Shell hooks (`TeammateIdle`, `TaskCompleted`)        | Merge pipeline: rebase → test → refinery review         |
| **Token tracking**       | Per-session only, no cross-agent aggregation         | `tokens_used` events, per-issue/per-run budgets         |
| **Stall detection**      | Manual — user notices teammate stopped               | Lease-based: expiry → status check → extend or fail     |
| **Concurrency control**  | Implicit (team size = teammate count)                | `MAX_AGENTS` cap + per-run token budget                 |
| **Dependency model**     | `blocks`/`blockedBy` on task files                   | DAG in `dependencies` table with `blocks` edges         |

### Deep Dive: Key Differences

#### 1. Decentralized vs Centralized

Claude Code Teams is **decentralized**. The lead is just another Claude session that happens to have team management tools. There is no background process. If the lead crashes, there is no recovery mechanism — in-process teammates cannot be resumed [^docs-limitations].

Hive is **centralized**. A Python orchestrator process (`orchestrator.py`) manages the entire lifecycle. This adds complexity but enables lease-based stall detection, automatic retry chains (retry → agent-switch → escalation), and clean-state reconciliation on restart [^hive-design-doc].

#### 2. The Merge Problem

This is Hive's biggest differentiator. Claude Code Teams has **no merge strategy**. All teammates edit files in the same working directory. The documentation explicitly warns: "Two teammates editing the same file leads to overwrites. Break the work so each teammate owns a different set of files" [^docs-conflicts].

Hive gives each worker its own git worktree and runs a two-tier merge pipeline [^hive-design-doc]:

1. **Mechanical**: `git rebase main` → test gate (worker command, then global command) → `git merge --ff-only`
2. **Refinery**: If mechanical merge fails (rebase conflict, test failure), an LLM session (typically Opus) resolves the conflict

This means Hive can safely schedule work that touches overlapping files. The merge pipeline is the safety net that enables aggressive parallelism.

#### 3. Filesystem vs Database

Claude Code's file-based approach is elegantly simple — zero infrastructure required. But it has real tradeoffs:

- **No transactional guarantees**: Task claiming relies on `flock()`, which provides mutual exclusion but not ACID semantics. A crash mid-write can leave an inbox file or task file in an inconsistent state.
- **No aggregate queries**: You can't easily answer "what's the total token spend across all agents?" or "what's the mean time-to-completion by model?"
- **No event log**: There's no audit trail of state transitions. When a task moves from `pending` to `in_progress`, there's no record of when, by whom, or why.
- **No resumption**: The docs explicitly state that `/resume` and `/rewind` do not restore in-process teammates [^docs-limitations].

Hive's SQLite gives ACID transactions, an append-only event ledger, rich metric views (`agent_runs`), and the ability to reconstruct state after crashes [^hive-design-doc]. The cost is needing a running process.

#### 4. Communication Models

**Claude Code**: Mailbox pattern. Write to recipient's inbox file → they poll it → message arrives as a synthetic conversation turn. Simple, but latency depends on poll interval. No delivery guarantees if a crash occurs between write and read.

**Hive**: Event-driven injection. The orchestrator monitors `.hive-notes.jsonl` in each worktree, harvests notes in real-time, persists them to the `notes` table, and relays them to other active workers via SSE/WS events [^hive-design-doc]. This provides both real-time coordination and durable cross-worker knowledge transfer.

#### 5. Token Budget Enforcement

**Claude Code**: No cross-agent token tracking. The only controls are manual team cleanup and model selection guidance [^docs-cost-optimization].

**Hive**: Tracks `tokens_used` events per agent, enforces per-issue budgets (`MAX_TOKENS_PER_ISSUE=200000`), and has a global per-run cap (`MAX_TOKENS_PER_RUN=2000000`). Worker spawning pauses when the budget is exhausted [^hive-design-doc].

---

## 8. Benchmarking Ideas

### Artificial Benchmarks

1. **SWE-bench Decomposition**: Take verified SWE-bench tasks that naturally split into subtasks. Run both systems with the same decomposition. Measure: wall-clock time, total tokens consumed, success rate, and merge conflict rate.

2. **Synthetic Conflict Test**: Create a repository where N issues intentionally touch overlapping files. Measure how each system handles conflicts:
   - Agent Teams: relies on task partitioning (fails if overlap occurs)
   - Hive: merge pipeline absorbs conflicts mechanically or via refinery

3. **Dependency Chain Throughput**: Create a DAG of tasks with varied dependency structures (wide-parallel, deep-serial, diamond). Measure scheduling efficiency — does the system correctly maximize parallelism while respecting ordering constraints?

4. **Recovery/Resilience Test**: Kill the orchestrator/lead mid-run. Measure recovery:
   - Agent Teams: teammates become orphans, no automatic recovery
   - Hive: reconciles stale agents on restart, reopens issues, cleans up worktrees

5. **Token Efficiency per Unit of Output**: Same coding task, measure total tokens consumed per line of correct, merged code. Hive's worktree isolation might save context that would otherwise be spent on conflict-avoidance instructions in Agent Teams.

### Natural Benchmarks

6. **Real Project Refactor**: Take a medium-sized open-source project. Define a refactoring task (e.g., "migrate from callbacks to async/await across all modules"). Run both systems. Compare: code quality, test pass rate, human review time.

7. **Agentic Coding Tournament**: Run both systems on the same set of 10-20 diverse tasks (bug fixes, features, refactors, documentation). Score on: success rate, token cost, wall-clock time, code quality (human-rated).

---

## 9. Conclusions

Claude Code Agent Teams is a remarkably simple system. The entire coordination layer is just JSON files on disk — no daemon, no database, no network protocol. This makes it portable, easy to understand, and zero-infrastructure. The tradeoff is fragility: no crash recovery, no merge strategy, no token budgets, no event log.

Hive takes the opposite approach: a centralized orchestrator with SQLite-backed state, git worktree isolation, a two-tier merge pipeline, and automatic retry/escalation. This handles the hard problems (merge conflicts, stall detection, token budgets, state recovery) at the cost of operational complexity.

The fundamental question is: **do you need a merge strategy?**

- If your tasks are cleanly partitioned across files and you trust the lead to manage conflicts manually, Agent Teams' simplicity is a real advantage.
- If your tasks overlap, require automated testing gates, or need to survive crashes, you need something like Hive's approach.

Both systems validate the same core insight: LLM agents are most effective when coordination logic is external to the model context. Whether that coordination lives in flat files or a SQL database is an engineering tradeoff, not a fundamental architectural difference. The real differentiator is what happens when things go wrong — and in multi-agent systems, things always go wrong.

---

## Sources

### Primary: Official Documentation

[^docs-arch]: Claude Code Docs, ["Orchestrate teams of Claude Code sessions — Architecture"](https://code.claude.com/docs/en/agent-teams#architecture). Accessed 2026-02-19.

[^docs-task-fields]: Claude Code Docs, ["Interactive mode — Task list"](https://code.claude.com/docs/en/interactive-mode#task-list). Accessed 2026-02-19. Task schema fields documented in tool definitions within Claude Code v2.1.47.

[^docs-claiming]: Claude Code Docs, ["Agent teams — Assign and claim tasks"](https://code.claude.com/docs/en/agent-teams#assign-and-claim-tasks): "Task claiming uses file locking to prevent race conditions when multiple teammates try to claim the same task simultaneously."

[^docs-task-order]: Claude Code Docs, tool definitions in binary: "Prefer tasks in ID order (lowest ID first) when multiple tasks are available."

[^docs-messaging]: Claude Code Docs, ["Agent teams — Context and communication"](https://code.claude.com/docs/en/agent-teams#context-and-communication).

[^docs-auto-delivery]: Claude Code Docs: "When teammates send messages, they're delivered automatically to recipients. The lead doesn't need to poll for updates."

[^docs-broadcast-cost]: Claude Code Docs: "broadcast: send to all teammates simultaneously. Use sparingly, as costs scale with team size."

[^docs-peer-dm]: Claude Code Docs: "When a teammate sends a DM to another teammate, a brief summary is included in the lead's idle notification."

[^docs-context]: Claude Code Docs, ["Agent teams — Context and communication"](https://code.claude.com/docs/en/agent-teams#context-and-communication): "Each teammate has its own context window. When spawned, a teammate loads the same project context as a regular session: CLAUDE.md, MCP servers, and skills."

[^docs-env-vars]: Claude Code Docs, ["Settings"](https://code.claude.com/docs/en/settings): `CLAUDE_CODE_TEAM_NAME` and `CLAUDE_CODE_PLAN_MODE_REQUIRED` documented as auto-set environment variables.

[^docs-idle]: Claude Code Docs: "After every turn, teammates go idle and the system automatically sends an idle notification to the lead. This is normal behavior, not an error."

[^docs-shutdown]: Claude Code Docs, ["Agent teams — Shut down teammates"](https://code.claude.com/docs/en/agent-teams#shut-down-teammates).

[^docs-cleanup]: Claude Code Docs, ["Agent teams — Clean up the team"](https://code.claude.com/docs/en/agent-teams#clean-up-the-team): "When the lead runs cleanup, it checks for active teammates and fails if any are still running."

[^docs-permissions]: Claude Code Docs, ["Agent teams — Permissions"](https://code.claude.com/docs/en/agent-teams#permissions): "Teammates start with the lead's permission settings."

[^docs-hooks]: Claude Code Docs, ["Hooks"](https://code.claude.com/docs/en/hooks). TeammateIdle and TaskCompleted hook events.

[^docs-task-hook]: Claude Code Docs, Hooks: "Fires in two situations: (1) when any agent explicitly marks a task completed via TaskUpdate, or (2) when an agent team teammate finishes its turn with in-progress tasks."

[^docs-costs]: Claude Code Docs, ["Costs — Agent team token costs"](https://code.claude.com/docs/en/costs#agent-team-token-costs): "Agent teams use approximately 7x more tokens than standard sessions when teammates run in plan mode."

[^docs-cost-optimization]: Claude Code Docs, Costs: "Use Sonnet for teammates", "Keep teams small", "Clean up teams when done."

[^docs-limitations]: Claude Code Docs, ["Agent teams — Limitations"](https://code.claude.com/docs/en/agent-teams#limitations): "/resume and /rewind do not restore in-process teammates."

[^docs-conflicts]: Claude Code Docs, ["Agent teams — Best practices"](https://code.claude.com/docs/en/agent-teams#avoid-file-conflicts): "Two teammates editing the same file leads to overwrites."

[^docs-interactive]: Claude Code Docs, ["Interactive mode"](https://code.claude.com/docs/en/interactive-mode). Task list UI, `Ctrl+T` toggle, `CLAUDE_CODE_TASK_LIST_ID` for cross-session sharing.

### Primary: On-Disk Artifacts (Claude Code v2.1.47)

[^disk-teams]: Team directory observed at `/Users/tau/.claude/teams/b18e107a-fc7a-414d-811c-9466dbdf1c3f/`. Contains `inboxes/` subdirectory with `cp-agent.json` and `web-agent.json`.

[^disk-inbox]: Inbox file at `/Users/tau/.claude/teams/b18e107a-fc7a-414d-811c-9466dbdf1c3f/inboxes/cp-agent.json`. Contains JSON array with `task_assignment` message from `team-lead`, timestamped `2026-02-18T02:37:16.890Z`.

[^disk-task-example]: Task file at `/Users/tau/.claude/tasks/71ea1281-d7fd-4fc9-8e34-2c82135f298b/1.json`. Schema: `{id, subject, description, activeForm, status, blocks, blockedBy}`.

[^disk-lock-hwm]: `.lock` (0-byte) and `.highwatermark` (integer string) files observed in all 42 task directories under `/Users/tau/.claude/tasks/`.

[^disk-task-dirs]: 42 task directories under `/Users/tau/.claude/tasks/`; only 5 contain task JSON files. Remaining 37 contain only `.lock` and `.highwatermark`.

### Primary: Binary Analysis

[^binary-analysis]: Claude Code binary v2.1.47. Internal functions identified via string analysis: `getTeamName`, `getAgentName`, `getAgentId`, `isTeammate`, `isTeamLead`, `waitForTeammatesToBecomeIdle`, `getTeammateContext`, `setDynamicTeamContext`, `createTeammateContext`. AsyncLocalStorage context fields: `agentId`, `agentName`, `teamName`, `parentSessionId`, `color`, `planModeRequired`.

### Primary: Hive Codebase

[^hive-design-doc]: Hive, [`docs/TECHNICAL_DESIGN_DOC.md`](docs/TECHNICAL_DESIGN_DOC.md). Last verified against code 2026-02-16. Covers: SQLite schema, orchestrator runtime, merge pipeline, backend abstraction, retry/escalation policy, token tracking.

### Secondary: Claude Code Documentation Index

- [Sub-agents](https://code.claude.com/docs/en/sub-agents) — lighter-weight single-session delegation
- [Settings](https://code.claude.com/docs/en/settings) — `teammateMode`, environment variables, permission configuration
- [Hooks](https://code.claude.com/docs/en/hooks) — lifecycle events including `TeammateIdle` and `TaskCompleted`
- [Costs](https://code.claude.com/docs/en/costs) — token usage guidance, ~$6/dev/day baseline, 7× multiplier for teams
- [Interactive mode](https://code.claude.com/docs/en/interactive-mode) — task list UI, `Ctrl+T` toggle
