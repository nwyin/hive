"""Queen Bee mixin for HiveCLI."""

import os
import subprocess
import sys
from pathlib import Path

from ..config import Config


class QueenMixin:
    """Mixin providing Queen Bee TUI methods for HiveCLI."""

    # Legacy sentinels — used only for cleaning up CLAUDE.md from older sessions
    _QUEEN_SENTINEL_START = "<!-- HIVE-QUEEN-SESSION-START -->"
    _QUEEN_SENTINEL_END = "<!-- HIVE-QUEEN-SESSION-END -->"

    _QUEEN_SYSTEM_PROMPT = (
        "You are the Queen Bee coordinator. You do NOT write code — you plan, decompose, and monitor.\n"
        "Full instructions: `.hive/queen-instructions.md` — re-read if your context feels incomplete.\n"
        "Project context: `.hive/project-context.md` — architecture, build, conventions, key files.\n"
        "Persistent context: `.hive/queen-context.md` — accumulated project knowledge across sessions.\n"
        "Operational state: `.hive/queen-state.md` — re-read to recall what you were working on.\n"
        "Always use `hive --json` for CLI commands. The daemon runs in background."
    )

    def _resolve_mcp_configs(self, configs: list[str] | None) -> list[str]:
        """Resolve bare MCP config names against ~/.claude."""
        resolved: list[str] = []
        for config in configs or []:
            path = Path(config).expanduser()
            if not path.is_absolute() and path.parent == Path("."):
                path = Path.home() / ".claude" / config
            resolved.append(str(path))
        return resolved

    def _ensure_daemon_running(self):
        """Start the daemon if needed and return its status."""
        daemon = self._make_daemon()
        daemon_status = daemon.status()
        if daemon_status["running"]:
            return daemon_status

        print("Starting daemon... ", end="", flush=True)
        daemon.start()
        daemon_status = daemon.status()
        if daemon_status["running"]:
            print(f"done (PID {daemon_status['pid']})")
            return daemon_status

        print("failed")
        self._error("Failed to start daemon. Check `hive logs --daemon`.")

    def queen(
        self,
        *,
        backend: str | None = None,
        skip_permissions: bool = False,
        mcp_configs: list[str] | None = None,
        headless: bool = False,
        prompt: str | None = None,
        mode: str | None = None,
    ):
        """Launch Queen Bee TUI using the configured backend."""
        # Propagate to daemon and workers via env var (before daemon.start())
        if skip_permissions or headless:
            os.environ["HIVE_CLAUDE_SKIP_PERMISSIONS"] = "1"
        resolved_mcp_configs = self._resolve_mcp_configs(mcp_configs)
        if resolved_mcp_configs:
            os.environ["HIVE_CLAUDE_MCP_CONFIGS"] = os.pathsep.join(resolved_mcp_configs)

        self._ensure_daemon_running()

        effective = backend or Config.QUEEN_BACKEND or Config.BACKEND
        if effective == "codex":
            self._queen_codex(headless=headless, prompt=prompt, mode=mode)
        else:
            self._queen_claude(
                skip_permissions=skip_permissions or headless,
                mcp_configs=resolved_mcp_configs,
                headless=headless,
                prompt=prompt,
                mode=mode,
            )

    def _queen_write_identity_files(self, mode: str | None = None) -> Path:
        """Write queen identity files and return the instructions path.

        The base queen-instructions.md is seeded by ``hive init`` and persists
        between sessions.  This method overwrites it with mode-specific content
        when a mode is active; cleanup restores the base version.
        """
        from ..prompts import _load_template

        from .runtime import do_seed_queen_files

        # Ensure base files exist (idempotent — mirrors what ``hive init`` does)
        do_seed_queen_files(self.project_path, json_mode=True)

        instructions_path = self.project_path / ".hive" / "queen-instructions.md"

        # Append mode addendum if active
        if mode:
            queen_prompt = _load_template("queen")
            mode_addendum = _load_template(f"queen_{mode}")
            instructions_path.write_text(f"{queen_prompt}\n\n{mode_addendum}")

        # Clean up legacy sentinel block from CLAUDE.md if present
        claude_md = self.project_path / ".claude" / "CLAUDE.md"
        if claude_md.exists():
            content = claude_md.read_text()
            if self._QUEEN_SENTINEL_START in content:
                self._remove_legacy_sentinel(claude_md, content)

        return instructions_path

    def _remove_legacy_sentinel(self, claude_md: Path, content: str):
        """Remove legacy sentinel block from CLAUDE.md (one-time migration)."""
        start = content.find(self._QUEEN_SENTINEL_START)
        if start == -1:
            return
        end = content.find(self._QUEEN_SENTINEL_END)
        if end == -1:
            return
        end += len(self._QUEEN_SENTINEL_END)
        if end < len(content) and content[end] == "\n":
            end += 1
        cleaned = (content[:start] + content[end:]).rstrip("\n")
        if cleaned.strip():
            claude_md.write_text(cleaned + "\n")
        else:
            claude_md.unlink()

    def _queen_cleanup_identity_files(self, instructions_path: Path):
        """Clean up ephemeral session files; restore base queen instructions."""
        from ..prompts import _load_template

        # Restore base instructions (strip any mode addendum written for this session)
        try:
            instructions_path.write_text(_load_template("queen"))
        except Exception:
            pass
        state_file = self.project_path / ".hive" / "queen-state.md"
        state_file.unlink(missing_ok=True)

    def _run_queen_process(
        self, cmd: list[str], launch_message: str, *, missing_error: str | None = None, headless: bool = False, mode: str | None = None
    ):
        """Run a queen subprocess with identity-file setup and cleanup."""
        instructions_path = self._queen_write_identity_files(mode=mode)
        print(launch_message)
        try:
            result = subprocess.run(cmd, cwd=str(self.project_path))
            if not headless:
                sys.exit(result.returncode)
            elif result.returncode != 0:
                self._error(f"Queen process exited with code {result.returncode}")
        except FileNotFoundError:
            if missing_error:
                self._error(missing_error)
            raise
        except KeyboardInterrupt:
            pass
        finally:
            self._queen_cleanup_identity_files(instructions_path)

    _HEADLESS_SYSTEM_PROMPT = (
        "You are running in HEADLESS MODE. There is no interactive user.\n"
        "- Skip the plan proposal step — create issues directly.\n"
        "- Do NOT ask questions or wait for approval.\n"
        "- Read .hive/project-context.md and .hive/queen-context.md for project knowledge.\n"
        "- Explore the codebase as needed to write good issue descriptions.\n"
        "- After creating issues, update .hive/queen-context.md with any new learnings.\n"
        "- Output a summary of created issues before exiting."
    )

    def _queen_claude(
        self,
        *,
        skip_permissions: bool = False,
        mcp_configs: list[str] | None = None,
        headless: bool = False,
        prompt: str | None = None,
        mode: str | None = None,
    ):
        """Launch Queen Bee as an interactive Claude CLI session."""
        os.environ.pop("CLAUDECODE", None)

        short_prompt = "You are the Hive Queen Bee coordinator. Read .hive/queen-instructions.md for your full instructions now."

        claude_cmd = os.environ.get("CLAUDE_CMD", "claude")
        cmd = [
            claude_cmd,
            "--model",
            Config.DEFAULT_MODEL,
            "--append-system-prompt",
            short_prompt,
            "--append-system-prompt",
            self._QUEEN_SYSTEM_PROMPT,
        ]

        if mode:
            cmd.extend(
                ["--append-system-prompt", f"You are in {mode.upper()} mode. Read .hive/queen-instructions.md for mode-specific instructions."]
            )

        if headless:
            cmd.extend(["--append-system-prompt", self._HEADLESS_SYSTEM_PROMPT])
            cmd.extend(["--print", "-p", prompt])
            cmd.append("--dangerously-skip-permissions")
        else:
            if skip_permissions:
                cmd.append("--dangerously-skip-permissions")
            else:
                cmd.extend(
                    [
                        "--allowedTools",
                        "Bash(hive:*) Bash(git:*) Bash(ls:*) Bash(find:*) Bash(rg:*) Read Edit Write",
                    ]
                )

        for config in mcp_configs or []:
            cmd.extend(["--mcp-config", config])

        label = "Launching Queen Bee headless...\n" if headless else "Launching Queen Bee TUI (Claude CLI)...\n"
        self._run_queen_process(cmd, label, headless=headless, mode=mode)

    def _queen_codex(self, *, headless: bool = False, prompt: str | None = None, mode: str | None = None):
        """Launch Queen Bee as an interactive Codex CLI session."""
        if headless:
            short_prompt = f"{self._HEADLESS_SYSTEM_PROMPT}\n\nTask: {prompt}"
        else:
            short_prompt = "Read .hive/queen-instructions.md for your full instructions now."

        developer_instructions = (
            "You are the Hive Queen Bee coordinator. You do NOT write code; you plan, decompose, and monitor.\\n"
            "Full instructions: .hive/queen-instructions.md (read now; re-read after compaction).\\n"
            "Persistent context: .hive/queen-context.md (accumulated project knowledge across sessions).\\n"
            "Operational state: .hive/queen-state.md (re-read after compaction; update after significant actions).\\n"
        )
        if mode:
            developer_instructions += f"You are in {mode.upper()} mode. Read .hive/queen-instructions.md for mode-specific instructions.\\n"
        if headless:
            developer_instructions += "HEADLESS MODE: Skip plan approval — create issues directly. Do NOT ask questions.\\n"
        else:
            developer_instructions += (
                "Before creating issues/epics, output a human-readable plan for user review and wait for explicit approval.\\n"
            )
        developer_instructions += "Always use hive --json for Hive CLI commands."

        compact_prompt = (
            "Summarize the conversation for continuity.\\n"
            "Preserve: user goals, key decisions, current plan/issues, and next steps.\\n"
            "Always include a reminder to read .hive/queen-instructions.md, .hive/queen-context.md, and .hive/queen-state.md after compaction."
        )

        codex_cmd = os.environ.get("CODEX_CMD", "codex")
        sandbox = os.environ.get("HIVE_CODEX_QUEEN_SANDBOX") or getattr(Config, "CODEX_SANDBOX", "workspace-write")
        approval = (
            "never" if headless else (os.environ.get("HIVE_CODEX_QUEEN_APPROVAL_POLICY") or getattr(Config, "CODEX_APPROVAL_POLICY", "never"))
        )
        cmd = [
            codex_cmd,
            "--sandbox",
            sandbox,
            "--ask-for-approval",
            approval,
            "-c",
            f'developer_instructions="{developer_instructions}"',
            "-c",
            f'compact_prompt="{compact_prompt}"',
            "--cd",
            str(self.project_path),
            short_prompt,
        ]

        label = "Launching Queen Bee headless (Codex)...\n" if headless else "Launching Queen Bee TUI (Codex CLI)...\n"
        self._run_queen_process(
            cmd,
            label,
            missing_error="Codex CLI not found. Install `codex` and ensure it's on PATH, or set CODEX_CMD to the codex executable path.",
            headless=headless,
            mode=mode,
        )
