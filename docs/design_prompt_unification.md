# Design: Queen Prompt Unification

## Problem

The Queen Bee prompt exists in three places:

| File | Tracked | Generated? | Backend |
|------|---------|------------|---------|
| `src/hive/prompts/queen.md` | git | — | Source of truth |
| `.hive/queen-instructions.md` | gitignored | Auto-generated at launch | Claude CLI |
| `.opencode/agents/queen.md` | gitignored | **Manual** copy | OpenCode |

The Claude backend already does the right thing — `cli.py:_queen_write_identity_files()` reads `src/hive/prompts/queen.md` and writes it to `.hive/queen-instructions.md` on every Queen launch. That copy is always in sync.

The OpenCode copy (`.opencode/agents/queen.md`) is manually maintained. It requires YAML frontmatter for OpenCode's agent system (permissions, tools, mode), and has drifted from the source (e.g., missing the STATE PERSISTENCE section). Any edit to the queen prompt requires updating this file by hand, which is brittle and error-prone.

## Architecture

### How each backend consumes the prompt

**Claude backend:**
1. `_queen_write_identity_files()` loads `src/hive/prompts/queen.md` via `_load_template("queen")`
2. Writes full content to `.hive/queen-instructions.md`
3. Launches Claude CLI with a short system prompt containing an identity anchor
4. The anchor in `.claude/CLAUDE.md` directs the Queen to read `.hive/queen-instructions.md`
5. File-based delivery — survives context compaction (agent re-reads the file)

**OpenCode backend:**
1. `_queen_opencode()` invokes `opencode attach <url> --dir <project>`
2. OpenCode loads agent definitions from `.opencode/agents/` directory
3. Parses YAML frontmatter (permissions, tools, mode) + markdown body
4. Server-side injection — prompt sent to model at session start

Key difference: Claude uses file-based prompt delivery (agent reads file during session), OpenCode uses server-side injection (server loads and sends prompt on start).

### OpenCode YAML frontmatter

The OpenCode agent file requires this metadata:

```yaml
---
description: Strategic coordinator for Hive multi-agent orchestration
mode: primary
tools:
  write: true
  edit: true
permission:
  bash:
    "hive *": allow
    "git *": allow
    "ls *": allow
    "find *": allow
    "rg *": allow
  read: allow
---
```

This is OpenCode-specific configuration that doesn't belong in the shared prompt source.

## Proposed Solution

**Generate `.opencode/agents/queen.md` from the source template at launch time**, same pattern as the Claude backend.

### Implementation

1. Store the OpenCode frontmatter as a constant in `cli.py` (or a small `queen_opencode.yaml` file)
2. In `_queen_opencode()` (or a parallel `_queen_write_opencode_agent()` function):
   - Load `src/hive/prompts/queen.md` via `_load_template("queen")`
   - Prepend the YAML frontmatter
   - Write to `.opencode/agents/queen.md`
3. On cleanup, optionally delete the generated file (or leave it — it's gitignored)

### Code sketch

```python
OPENCODE_QUEEN_FRONTMATTER = """\
---
description: Strategic coordinator for Hive multi-agent orchestration
mode: primary
tools:
  write: true
  edit: true
permission:
  bash:
    "hive *": allow
    "git *": allow
    "ls *": allow
    "find *": allow
    "rg *": allow
  read: allow
---

"""

def _queen_write_opencode_agent(self):
    queen_prompt = _load_template("queen")
    agents_dir = self.project_path / ".opencode" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    agent_file = agents_dir / "queen.md"
    agent_file.write_text(OPENCODE_QUEEN_FRONTMATTER + queen_prompt)
```

### Result

- **One source of truth**: `src/hive/prompts/queen.md`
- **Two derived copies**: both generated at launch, always in sync
- **Zero manual maintenance**: edits to queen instructions only touch the source file

## Complexity

Small — single file change in `cli.py`, ~15 lines of code. The pattern already exists for the Claude backend.

## Risks

- If OpenCode caches agent files across sessions, the generated file might be stale. Mitigated by always regenerating on launch.
- If the YAML frontmatter needs to change (new permissions, tools), it's in Python code rather than a standalone file. Acceptable tradeoff for a rarely-changing config.
