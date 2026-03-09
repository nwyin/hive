"""Rich-based human output for Typer-backed commands."""

from __future__ import annotations

import json

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


def print_create(console: Console, result: dict) -> None:
    """Render create output."""
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan")
    table.add_column()
    table.add_row("Issue", result["id"])
    table.add_row("Title", result["title"])
    table.add_row("Priority", str(result["priority"]))
    if result.get("tags"):
        table.add_row("Tags", ", ".join(result["tags"]))
    if result.get("depends_on"):
        table.add_row("Depends on", ", ".join(result["depends_on"]))

    console.print(Panel.fit(table, title="Created", border_style="green"))


def print_issue_list(console: Console, result: dict) -> None:
    """Render the issue list."""
    issues = result.get("issues", [])
    if not issues:
        console.print("No issues found.")
        console.print("Create one with: hive create 'title' 'description'", style="dim")
        return

    table = Table(box=box.SIMPLE_HEAVY, header_style="bold")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Status", style="magenta")
    table.add_column("Pri", justify="right")
    table.add_column("Type")
    table.add_column("Title", overflow="fold")

    for issue in issues:
        table.add_row(
            issue["id"],
            issue["status"],
            str(issue["priority"]),
            str(issue.get("type", ""))[:10],
            str(issue["title"]),
        )

    console.print(table)
    console.print(f"Total: {len(issues)} issues", style="dim")


def print_issue_show(console: Console, result: dict) -> None:
    """Render a single issue detail view."""
    summary = Table.grid(padding=(0, 2))
    summary.add_column(style="bold cyan")
    summary.add_column()
    summary.add_row("Issue", result["id"])
    summary.add_row("Title", result["title"])
    summary.add_row("Status", result["status"])
    summary.add_row("Priority", str(result["priority"]))
    summary.add_row("Type", result["type"])
    summary.add_row("Assignee", result["assignee"] or "None")
    if result.get("tags"):
        summary.add_row("Tags", ", ".join(result["tags"]))
    if result.get("model"):
        summary.add_row("Model", str(result["model"]))
    summary.add_row("Created", str(result["created_at"]))

    console.print(Panel.fit(summary, title="Issue", border_style="blue"))

    if result.get("description"):
        console.print(Panel(result["description"], title="Description", border_style="white"))

    dependencies = result.get("dependencies", [])
    if dependencies:
        dep_table = Table(box=box.MINIMAL_HEAVY_HEAD)
        dep_table.add_column("Depends on", style="cyan", no_wrap=True)
        dep_table.add_column("Status", style="magenta")
        dep_table.add_column("Title")
        for dep in dependencies:
            dep_table.add_row(dep["id"], dep["status"], dep["title"])
        console.print(dep_table)

    events = result.get("recent_events", [])
    if events:
        event_table = Table(box=box.SIMPLE)
        event_table.add_column("When", style="dim", no_wrap=True)
        event_table.add_column("Event", style="bold")
        event_table.add_column("Detail")
        for event in events[:10]:
            detail = ""
            if event.get("detail"):
                parsed = json.loads(event["detail"]) if isinstance(event["detail"], str) else event["detail"]
                detail = ", ".join(f"{key}={value}" for key, value in parsed.items())
            event_table.add_row(str(event["created_at"]), str(event["event_type"]), detail)
        console.print(event_table)


def print_error(console: Console, message: str) -> None:
    """Render an error message."""
    console.print(Text(f"Error: {message}", style="bold red"))
