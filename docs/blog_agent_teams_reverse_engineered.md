# Reverse-Engineering Claude Code Agent Teams: Architecture, Protocol, and Comparison with Hive

_A technical analysis of Anthropic's experimental multi-agent coordination system._

## Introduction

Claude Code (v2.1.47) ships with an experimental feature called **Agent Teams**: multiple Claude Code sessions coordinate on shared work through a lead-and-teammates topology. I've been building [Hive](https://github.com/your/hive), a multi-agent coding orchestrator with similar goals but a very different architecture, so I wanted to understand how Anthropic's approach works under the hood.

This post documents what I found through:

1. Reading the [official documentation](https://code.claude.com/docs/en/agent-teams)
2. Examining actual artifacts left on disk by previous team sessions
3. Letting Claude analyze the Claude Code binary (v2.1.47) for implementation details (hah!)
4. Comparing the architecture to Hive's SQLite + orchestrator approach

## Table of Contents

- [1. Architecture Overview](#1-architecture-overview)
- [2. The Shared Task List](#2-the-shared-task-list)
- [3. Inter-Agent Communication](#3-inter-agent-communication)
- [4. Agent Spawning and Lifecycle](#4-agent-spawning-and-lifecycle)
- [5. Quality Gates and Hooks](#5-quality-gates-and-hooks)
- [6. Token Economics](#6-token-economics)
- [7. Architectural Comparison: Agent Teams vs Hive](#7-architectural-comparison-agent-teams-vs-hive)
- [8. Conclusions](#8-conclusions)
- [Sources](#sources)

## 1. Architecture Overview

An agent team consists of four components [^docs-arch]:

| Component     | Role                                                                 |
| :------------ | :------------------------------------------------------------------- |
| **Team lead** | The main Claude Code session that creates the team, spawns teammates |
| **Teammates** | Separate Claude Code instances, each with its own context window     |
| **Task list** | Shared work items stored as individual JSON files on disk            |
| **Mailbox**   | Per-agent inbox files for message delivery                           |

The entire coordination layer is **file-based**. The filesystem at `~/.claude/` is the sole coordination substrate [^docs-arch]:

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

This is a fundamentally **decentralized** design. The lead is just another Claude session with extra tools (`TeamCreate`, `TeamDelete`, `SendMessage`). There is no background process. Coordination emerges from shared file access.

In an active session, if you ask Claude to spin up a team to do some kind of task and then run the following in another window, you can see the observe the filesystem update in real time.

```
watch -n 0.5 'tree ~/.claude/teams/ 2>/dev/null; echo "---"; tree ~/.claude/tasks/ 2>/dev/null'
```

For example, with the following prompt:

```
can you spanw an agent team to examine this code base?
  - have one look for bugs
  - have one look for complexity
  - have one look for good things to call out and play devil's advocate against the other two agents
```

I observed this:

```
teams
└── code-review
    ├── config.json
    └── inboxes
        ├── bug-hunter.json
        ├── complexity-analyst.json
        ├── devils-advocate.json
        └── team-lead.json
```

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

Names are the primary addressing mechanism (UUIDs exist but aren't used for routing). All messaging and task assignment uses the `name` field [^docs-arch].

## 2. The Shared Task List

### File Format

Each task is stored as an individual JSON file in `~/.claude/tasks/{team-name}/`. Here's a real example from a previous session [^disk-task-example]:

```json
{
  "id": "1",
  "subject": "Hunt for bugs across the codebase",
  "description": "...",
  "activeForm": "Hunting for bugs",
  "owner": "bug-hunter",
  "status": "completed",
  "blocks": [],
  "blockedBy": []
}
```

**Task schema** [^docs-task-fields]:

| Field         | Type     | Description                                                   |
| :------------ | :------- | :------------------------------------------------------------ |
| `id`          | string   | Numeric ID, auto-incremented via `.highwatermark`             |
| `subject`     | string   | Imperative-form title (e.g., "Run tests")                     |
| `description` | string   | Detailed requirements and acceptance criteria                 |
| `activeForm`  | string   | Present-continuous form for spinner display ("Running tests") |
| `status`      | string   | `pending` → `in_progress` → `completed` (or `deleted`)        |
| `blocks`      | string[] | Task IDs that this task blocks                                |
| `blockedBy`   | string[] | Task IDs that must complete before this task can start        |

### Concurrency Control

Two special files provide coordination [^disk-lock-hwm]:

- **`.lock`**: A 0-byte file used for filesystem-level mutual exclusion (`flock()`). Present in all 42 task directories observed on my machine.
- **`.highwatermark`**: Contains a single integer (e.g., `"3"`, `"13"`). The next available task ID for auto-incrementing.

### Task Claiming

Task claiming uses file locking to prevent race conditions [^docs-claiming]. Teammates prefer lowest-ID-first ordering [^docs-task-order]. A task with a non-empty `blockedBy` array cannot be claimed until all blocking tasks are in a terminal state.

### Observation: Most Task Directories Are Empty

Of 42 task directories on my machine, only 5 contained actual task JSON files [^disk-task-dirs]. The remaining 37 had only `.lock` and `.highwatermark`. This likely means tasks are cleaned up after completion, or these were sessions where Claude used the internal task list (available since the task list feature launch [^docs-interactive]) without decomposing into subtask files.

## 3. Inter-Agent Communication

### Mailbox Pattern

Each agent has a JSON array file at `~/.claude/teams/{team-name}/inboxes/{agent-name}.json`. Here's a real inbox from a previous session where a team-lead dispatched work to a `controlplane-agent` [^disk-inbox]:

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

| Type                     | Direction       | Purpose                             |
| :----------------------- | :-------------- | :---------------------------------- |
| `task_assignment`        | lead → teammate | Assign a task with full details     |
| `message`                | any → any       | Direct message to one recipient     |
| `broadcast`              | lead → all      | Same message to every teammate      |
| `shutdown_request`       | lead → teammate | Request graceful shutdown           |
| `shutdown_response`      | teammate → lead | Approve or reject shutdown          |
| `plan_approval_request`  | teammate → lead | Submit plan for review              |
| `plan_approval_response` | lead → teammate | Approve or reject with feedback     |
| `idle_notification`      | teammate → lead | Auto-sent when teammate's turn ends |

### Delivery Mechanism

**Write path**: The sender appends a new entry to the recipient's inbox JSON array file.

**Read path**: The recipient polls their own inbox file. New messages are injected as synthetic conversation turns (they appear as if a user sent them) [^docs-auto-delivery].

**Broadcast**: Literally writes the same message to every teammate's inbox file. Token cost scales linearly with team size [^docs-broadcast-cost].

Communicatoin is just file append + file read. Latency between send and receive depends on the recipient's poll interval.

### Peer DM Visibility

When a teammate sends a DM to another teammate, a brief summary is included in the lead's idle notification. This gives the lead visibility into peer collaboration without the full message content [^docs-peer-dm].

## 4. Agent Spawning and Lifecycle

### How Teammates Are Created

Each teammate is a **separate `claude` CLI process** [^docs-arch]. The lead spawns them via the `Task` tool with `team_name` and `name` parameters. Environment variables are set on the spawned process [^docs-env-vars]:

- `CLAUDE_CODE_TEAM_NAME`: auto-set on spawned teammates
- `CLAUDE_CODE_PLAN_MODE_REQUIRED`: set to `true` if plan approval is required

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

- `isTeammate()` / `isTeamLead()`: role detection
- `waitForTeammatesToBecomeIdle()`: synchronization primitive for the lead
- `getTeammateContext()` / `setDynamicTeamContext()`: runtime context management

### Idle Detection

After every LLM turn, a teammate automatically goes idle and sends an `idle_notification` to the lead [^docs-idle]. This is the normal resting state, not an error condition. Sending a message to an idle teammate wakes it (the next poll cycle picks up the inbox message).

### Shutdown Protocol

1. Lead sends `shutdown_request` to a teammate [^docs-shutdown]
2. Teammate can approve (exits gracefully) or reject (continues working with an explanation)
3. Team cleanup via `TeamDelete` removes `~/.claude/teams/{team-name}/` and `~/.claude/tasks/{team-name}/`
4. Cleanup fails if any teammates are still active; they must be shut down first [^docs-cleanup]

### Permission Inheritance

Teammates inherit the lead's permission mode at spawn time. If the lead runs `--dangerously-skip-permissions`, all teammates do too [^docs-permissions]. Individual modes can be changed post-spawn but not configured per-teammate at spawn time.

## 5. Quality Gates and Hooks

Agent Teams integrates with Claude Code's hook system for quality enforcement [^docs-hooks]:

### TeammateIdle Hook

Fires when a teammate is about to go idle. Exit code 2 sends stderr as feedback and prevents idle, keeping the teammate working.

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

| Type      | Description                                            |
| :-------- | :----------------------------------------------------- |
| `command` | Shell script. JSON on stdin, exit codes for decisions. |
| `prompt`  | Single-turn LLM evaluation. Returns `{ok, reason}`.    |
| `agent`   | Multi-turn subagent with read tools. Up to 50 turns.   |

## 6. Token Economics

Agent teams use **approximately 7× more tokens** than standard sessions when teammates run in plan mode [^docs-costs]. Each teammate maintains its own full context window as a separate Claude instance.

### Baseline Reference

- Average Claude Code usage: ~$6/developer/day [^docs-costs]
- Agent teams: roughly proportional to team size on top of baseline

## 7. Architectural Summary Table

| Dimension                  | Claude Code Agent Teams                             |
| :------------------------- | :-------------------------------------------------- |
| **Coordination substrate** | Flat files (`~/.claude/tasks/`, `~/.claude/teams/`) |
| **Task format**            | One JSON file per task + `.lock` for claiming       |
| **Messaging**              | JSON inbox files (append + poll)                    |
| **Agent lifecycle**        | Self-managing CLI processes                         |
| **Work isolation**         | Shared working directory                            |
| **Merge strategy**         | None (agents edit files directly)                   |
| **Retry/escalation**       | Manual (lead decides, or user intervenes)           |
| **Topology**               | Lead + flat peers, peer-to-peer messaging           |
| **Scheduling**             | Self-claim (teammates grab next task)               |
| **State durability**       | Files only; no in-process teammate resumption       |
| **Quality gates**          | Shell hooks (`TeammateIdle`, `TaskCompleted`)       |
| **Token tracking**         | Per-session only, no cross-agent aggregation        |
| **Stall detection**        | Manual (user notices teammate stopped)              |
| **Concurrency control**    | Implicit (team size = teammate count)               |
| **Dependency model**       | `blocks`/`blockedBy` on task files                  |

## Sources

### Primary: Official Documentation

[^docs-arch]: Claude Code Docs, ["Teams of Claude Code sessions: Architecture"](https://code.claude.com/docs/en/agent-teams#architecture). Accessed 2026-02-19.

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
