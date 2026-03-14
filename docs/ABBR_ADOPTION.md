# ABBR Adoption Guide

Concrete rename plan for aligning the codebase with `docs/ABBR.md`.
Each commit should be incremental, independently safe to land, and worth the
churn it causes.

---

## Current State

| Abbreviation        | Existing uses | Remaining full-word | Adoption |
| ------------------- | ------------- | ------------------- | -------- |
| `ctx` ← `context`   | 102           | 1                   | 99%      |
| `msg` ← `message`   | 58            | 9                   | 87%      |
| `res` ← `result`    | 0             | 24                  | 0%       |
| `evt` ← `event`     | 0             | 0 (all excluded)    | clean    |
| `exc` ← `exception` | 1             | 1                   | 50%      |

Additionally, one function has a stuttering name that should be cleaned up:
`count_events_since_minutes_since_reset` → `count_events_in_window_after_reset`.

---

## Adoption Bias

This guide is intentionally narrower than a blanket search-and-replace.

Keep the rename when it:

- shortens a local scratch variable
- improves scan-ability in a hot path
- does not touch a durable or shared interface

Skip the rename when it:

- only changes a semantically meaningful local like `result` in domain code
- touches abstract method parameter names or other shared call signatures
- churns CLI-facing code without making it clearer

---

## Exclusion Rules

Do **not** rename any of the following, even if they match the target word:

| Pattern | Reason | Example |
| ------- | ------ | ------- |
| `asyncio.Event` instances | Domain type, not an "event" local | `event = self.session_status_events.get(session_id)` |
| `render_*` function params | Textual widget convention; `result` is the display payload | `render_issue_detail(result)` |
| Class / type names | ABBR.md § Dataclass / Type / Enum Naming | `CompletionResult`, `AgentIdentity` |
| Dict keys / JSON payload keys | ABBR.md § Event / DB / CLI Rule | `{"result": ...}`, `result=CompletionResult(...)` |
| String literals / log messages | Not code identifiers | `"context"`, `%(message)s` |
| Function / method names | ABBR.md § Function Naming — full words | `read_result_file`, `get_messages`, `send_message` |
| DB columns / event names | Long-lived interfaces | `event_type`, `session_id` |
| Abstract method params / shared backend interfaces | Avoid compatibility churn for little gain | `reply_permission(message=...)` |
| Compound domain names | Meaning changes | `project_context`, `context_path`, `file_result` |
| Note category enum `"context"` | Persisted value in DB | `db/notes.py`, `cli/typer_app.py` |
| CLI command payload locals | Usually the actual command result, not scratch data | `result = {"status": "started"}` |

---

## Commit Sequence

### Commit 1 — `context` → `ctx` + `exception` → `exc`

Smallest change. Warm-up commit to verify the rename process is clean.

| File | Line | Before | After |
| ---- | ---- | ------ | ----- |
| `src/hive/prompts.py` | 153 | `context = "\n".join(context_parts)` | `ctx = "\n".join(context_parts)` |
| `src/hive/prompts.py` | 175 | `context=context,` | `context=ctx,` |
| `src/hive/orchestrator/core.py` | 501 | `exception = task.exception()` | `exc = task.exception()` |

Update all downstream references to the renamed variables in the same functions.

**Verify:**
```sh
uvx ruff check src/hive/prompts.py src/hive/orchestrator/core.py
uv run pytest tests/test_orchestrator.py -q
```

---

### Commit 2 — `message` / `messages` → `msg` / `msgs` for locals only

| File | Line | Before | After |
| ---- | ---- | ------ | ----- |
| `src/hive/orchestrator/core.py` | 379 | `messages: List[Dict[str, Any]]` (param) | `msgs: List[Dict[str, Any]]` |
| `src/hive/orchestrator/core.py` | 391 | `for message in messages:` | `for msg in msgs:` |
| `src/hive/orchestrator/completion.py` | 81 | `messages = await self.backend.get_messages(...)` | `msgs = await self.backend.get_messages(...)` |
| `src/hive/merge.py` | 451 | `messages = await self.backend.get_messages(...)` | `msgs = await self.backend.get_messages(...)` |
| `src/hive/backends/backend_claude.py` | 259 | `messages = session.messages` | `msgs = session.messages` |
| `src/hive/backends/backend_claude.py` | 261 | `messages = messages[-limit:]` | `msgs = msgs[-limit:]` |

Do **not** rename the `reply_permission(message=...)` parameter in
`base.py`, `backend_claude.py`, `backend_codex.py`, or `tests/fake_backend.py`.
That is a shared interface, and the churn is not worth it.

Update all downstream references within each function body.

**Verify:**
```sh
uvx ruff check src/hive/orchestrator/ src/hive/merge.py src/hive/backends/
uv run pytest tests/test_orchestrator.py tests/test_merge.py tests/test_claude_ws.py -q
```

---

### Commit 3 — Rename `count_events_since_minutes_since_reset`

The name stutters (`since...since`). This is high-signal and low-risk.

| File | Line | Change |
| ---- | ---- | ------ |
| `src/hive/db/metrics.py` | 373 | Method definition: `count_events_since_minutes_since_reset` → `count_events_in_window_after_reset` |
| `src/hive/orchestrator/completion.py` | 310 | Call site |
| `src/hive/orchestrator/completion.py` | 339 | Call site |

3 changes total (1 definition + 2 call sites).

**Verify:**
```sh
rg 'count_events_since_minutes_since_reset' src/ tests/  # should return 0 matches
rg 'count_events_in_window_after_reset' src/             # should return 3 matches
uv run pytest tests/test_orchestrator.py tests/test_cost_guardrails.py -q
```

---

### Commit 4 — `result` → `res` for scratch / subprocess locals only

This phase is intentionally narrow. Only rename obvious scratch values where
`res` is standard and clearer enough:

| File | Line | Before | After |
| ---- | ---- | ------ | ----- |
| `src/hive/git.py` | 21 | `result = subprocess.run(...)` | `res = subprocess.run(...)` |
| `src/hive/git.py` | 100 | `result = subprocess.run(...)` | `res = subprocess.run(...)` |
| `src/hive/git.py` | 148 | `result = subprocess.run(...)` | `res = subprocess.run(...)` |
| `src/hive/git.py` | 166 | `result = _run_git(...)` | `res = _run_git(...)` |
| `src/hive/merge.py` | 242 | `result = await self._send_to_refinery_inner(...)` | `res = await self._send_to_refinery_inner(...)` |
| `src/hive/merge.py` | 254 | `result = await self._send_to_refinery_inner(...)` | `res = await self._send_to_refinery_inner(...)` |
| `src/hive/merge.py` | 264 | `result = { ... }` | `res = { ... }` |
| `src/hive/merge.py` | 409 | `result = await self._wait_for_refinery(...)` | `res = await self._wait_for_refinery(...)` |
| `src/hive/config.py` | 105 | `result = []` | `res = []` |
| `src/hive/backends/backend_codex.py` | 218 | `result = await self._request(...)` | `res = await self._request(...)` |
| `src/hive/prompts.py` | 201 | `result = base.rstrip()` | `res = base.rstrip()` |
| `src/hive/utils.py` | 144 | `result = subprocess.run(...)` | `res = subprocess.run(...)` |
| `src/hive/diag.py` | 53 | `result = subprocess.run(...)` | `res = subprocess.run(...)` |
| `src/hive/diag.py` | 64 | `result = subprocess.run(...)` | `res = subprocess.run(...)` |
| `src/hive/daemon.py` | 89 | `result = subprocess.run(...)` | `res = subprocess.run(...)` |

Skip domain-heavy cases where `result` carries actual business meaning, such as:

- `CompletionResult`
- `decision.result`
- `result = assess_completion(...)` in `orchestrator/completion.py`
- command payload locals in CLI code

Update all downstream references (`result.returncode` → `res.returncode`, etc.)
within each function scope.

**Verify:**
```sh
uvx ruff check src/hive/git.py src/hive/merge.py src/hive/config.py \
  src/hive/backends/backend_codex.py src/hive/prompts.py src/hive/utils.py \
  src/hive/diag.py src/hive/daemon.py
uv run pytest tests/test_git.py tests/test_merge.py tests/test_claude_ws.py -q
```

---

### Skipped On Purpose — CLI `result` locals

Do **not** mass-rename `result` to `res` in CLI modules.

Examples like these are semantically meaningful and read fine as `result`:

- `result = get_global_status(db)` in `cli/typer_app.py`
- `result = {"status": "started", ...}` in `cli/typer_app.py`
- `result = self.invoke_raw(...)` in `cli/core.py`

The only acceptable CLI exceptions are obvious subprocess scratch values such as
`subprocess.run(...)` results in very small functions.

---

## What Requires No Changes

### `event` → `evt`

All 3 local variables named `event` in the codebase are `asyncio.Event` instances
(excluded per rules above). No `evt` adoption is needed — this abbreviation is already clean.

| File | Line | Why excluded |
| ---- | ---- | ------------ |
| `orchestrator/core.py` | 93 | `asyncio.Event` from `session_status_events` |
| `orchestrator/lifecycle.py` | 369 | `asyncio.Event` from `session_status_events` |
| `orchestrator/lifecycle.py` | 639 | `asyncio.Event` from `session_status_events` |

Function parameters named `event` (in `_format_event`, `_event_to_json`, `_parse_event_detail`)
are excluded per the function-parameter rule.

---

## Verification Strategy

Do not run the entire test suite after every tiny rename commit.

Use:

1. `uvx ruff check` on the touched files for every commit
2. targeted tests for the touched subsystem
3. one full validation pass at the end:

```sh
uv run pytest tests/ -q
uv run pytest -m integration -q
```

---

## Summary

| Commit | Abbr | Files touched | Renames |
| ------ | ---- | ------------- | ------- |
| 1 | `ctx` + `exc` | 2 | 3 |
| 2 | `msg` / `msgs` locals | 5 | small/local only |
| 3 | function rename | 2 | 3 |
| 4 | `res` scratch locals | selective | selective |
| Skipped | CLI `result` locals | 0 | 0 |
| **Total** | | **smaller, selective set** | **TBD after exact sweep** |
