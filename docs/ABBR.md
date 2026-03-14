# ABBR

inspired by https://docs.fast.ai/dev/abbr.md

Abbreviate only when the short form is easier to read than the long form.

If the reader has to pause, spell it out.

## Why This Exists

Hive has two competing pressures:

- the orchestrator and backend code benefits from terse locals in hot paths
- the project also has durable interfaces that must stay explicit and grep-able

So this file is not a generic “rename everything shorter” policy. It exists to
help future edits preserve the current balance:

- short locals where the meaning is obvious
- full words for domain concepts and external surfaces
- no clever project-specific shorthand

This is also intended as context for future LLM edits. If you are changing code,
do not treat abbreviation as a blanket cleanup pass. Use it selectively.

## Current Project Context

As of this pass, the codebase already leans in a few clear directions:

- `ctx` is already the normal short form for `context`
- `msg` / `msgs` are already common for local message variables
- `exc` exists but is not fully adopted yet
- `res` is mostly *not* adopted yet, and should be used selectively
- `evt` is effectively unused because current `event` locals are mostly
  `asyncio.Event` instances, which we keep spelled out as `event`

The practical takeaway:

- adding more `ctx` and `msg` usage in small scopes is usually fine
- adding `res` everywhere is not the style
- renaming shared interfaces just to shorten them is not the style

## What We Optimize For

Short local names, obvious control flow, low visual noise in hot paths, and
stable repeated abbreviations. We leave public APIs, core domain words, and
project-specific shorthand uncompressed.

## Scope Rule

The smaller the scope, the shorter the name can be.

- loop / 3-10 line local: short names are fine
- helper body: use standard abbreviations only
- function names / method names: mostly full words
- public APIs / CLI / DB schema / event names: spell things out

Examples:

- good local: `cfg`, `ctx`, `msg`, `res`, `err`, `evt`
- good method: `read_result_file`, not `read_res_file`
- good DB column: `session_id`, not `sess_id`

## Migration Rationale

When deciding whether to shorten a name, use this order:

1. Is it a tiny local or scratch variable?
2. Is the abbreviation already standard in this repo?
3. Does the rename avoid touching a shared interface?
4. Does it make the code easier to scan, not just shorter?

If the answer to any of those is “no”, keep the longer name.

This is why:

- `context -> ctx` is usually good
- `messages -> msgs` is often good for locals
- `result -> res` is only good for scratch values like subprocess output or
  short helper temporaries
- `message` in an abstract backend method signature is *not* a good rename
- CLI payload locals named `result` are usually fine as-is

## Domain Rule

Do not abbreviate the core Hive nouns in persisted or cross-module code:

- `agent`
- `issue`
- `session`
- `worktree`
- `project`
- `backend`
- `orchestrator`

These are the words the codebase is built around. Saving 2-5 chars is not
worth the readability loss.

So prefer:

- `agent_id`, not `agt_id`
- `issue_id`, not `iss_id`
- `session_id`, not `sess_id`
- `worktree`, not `wt`
- `orchestrator`, not `orch`, in type/class/module names

Local exceptions are okay when the scope is tiny and the meaning is already
established in the surrounding code. Example: a 5-line local `sess` inside a
backend adapter is fine. A method named `cleanup_sess` is not.

## Allowed Standard Abbreviations

These are fine almost everywhere in local code:

| Abbr              | Meaning         |
| ----------------- | --------------- |
| `cfg`             | config          |
| `ctx`             | context         |
| `db`              | database        |
| `msg` / `msgs`    | message(s)      |
| `res`             | result          |
| `resp`            | response        |
| `req`             | request         |
| `err`             | error           |
| `exc`             | exception       |
| `evt`             | event           |
| `op`              | operation       |
| `fn`              | function        |
| `idx`             | index           |
| `num`             | number          |
| `ts`              | timestamp       |
| `repo`            | repository      |
| `tmp`             | temporary       |
| `args` / `kwargs` | Python-standard |

Compounds like `cfg_path`, `evt_type`, `ctx_mgr`, `resp_json` are fine in
local code but not ideal for public surfaces.

## Avoid These

Avoid abbreviations that are ambiguous or too project-specific:
`wt`, `iss`, `agt`, `sess`, `proj`, `orch`, `recon`, `compl`, `decomp`, `perm`.

None of these are banned forever, but they need a clear local reason. Default
to the full word.

## Function Naming

Function names should read like plain English. Avoid compressed shell-alias style.

Prefer:

- `handle_agent_complete`
- `check_stalled_agents`
- `cleanup_session`
- `read_result_file`

Avoid:

- `handle_agt_done`
- `chk_stalled_agts`
- `cleanup_sess`
- `read_res_file`

## Dataclass / Type / Enum Naming

Type names should almost always use full words.

Prefer:

- `AgentIdentity`
- `CompletionDecision`
- `StalledTransition`

Avoid:

- `AgentCtx`
- `CompDecision`
- `StallXn`

One exception: established technical terms are fine:

- `Config`
- `CLI`
- `JSON`
- `HTTP`
- `SSE`

## Event / DB / CLI Rule

Never use cute abbreviations in:

- event names
- database columns
- issue status values
- JSON payload keys that users/scripts may inspect
- CLI flags and subcommands

These are long-lived interfaces. Optimize for grep-ability and clarity over
line count.

That rule also applies to:

- abstract method parameter names
- backend interfaces shared across implementations
- persisted note categories and config keys

## Good Hive Examples

Good:

```py
cfg = Config
evt = self.db.get_latest_event(issue_id)
res = assess_completion(file_result=file_result)
ctx = SpawnContext(issue=issue, issue_id=issue_id, model=model)
```

Good:

```py
for idx, agent in enumerate(active_agents):
    msg = await self.backend.get_messages(agent.session_id, directory=agent.worktree)
```

Not good:

```py
agt = self.active_agents.get(agt_id)
iss = self.db.get_issue(iss_id)
wt = agent.worktree
recon_res = self._reconcile_stale_agt(agt)
```

Good in this repo:

```py
msgs = await self.backend.get_messages(agent.session_id, directory=agent.worktree)
for msg in msgs:
    ...
```

Good in this repo:

```py
res = subprocess.run(cmd, capture_output=True, text=True)
if res.returncode != 0:
    ...
```

Not good in this repo:

```py
async def reply_permission(self, request_id: str, reply: str, msg: str | None = None):
    ...
```

That is a shared interface, not a local scratch variable.

Not good in this repo:

```py
result = get_global_status(db)
res = {"status": "started"}
```

Those CLI payloads are already semantically clear as `result`.

## Translation Rule

If you introduce an abbreviation, the reader should be able to translate it to
the full word instantly and consistently.

Bad:

- `st` could mean status, state, session type, stall, step
- `data` when the variable is actually one specific event
- `obj` when the variable has a real domain meaning

Good:

- `status`
- `state`
- `evt`
- `row`

## LOC Cheat Code, Used Carefully

Yes, abbreviations can cut LOC.

That only counts as a win when all of these are true:

1. the abbreviation is standard or obvious
2. the scope is small
3. the shorter name makes the code denser but not harder to scan
4. the same abbreviation means the same thing everywhere

If not, keep the longer name.

## Default Bias

When in doubt:

- shorten infrastructure words
- keep domain words
- shorten locals
- keep interfaces explicit

That gets most of the benefit without turning the codebase into puzzle text.

## Short Version For Future Edits

If you are making code changes and need the fast rule:

- `ctx`, `msg`, `msgs`, `cfg`, `err`, `exc` are usually fine in locals
- `res` is fine for scratch values, not for every `result`
- keep `agent`, `issue`, `session`, `worktree`, `project`, `backend`,
  `orchestrator` spelled out
- do not abbreviate CLI, DB, event, JSON, or abstract-interface surfaces
