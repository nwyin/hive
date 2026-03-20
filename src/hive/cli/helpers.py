"""Shared helpers for CLI command implementations."""

from __future__ import annotations

from ..db import Database


def _enrich_agents_with_issues(db: Database, agents: list[dict]) -> list[dict]:
    """Return a worker-summary list with current issue titles resolved.

    For each agent dict, looks up the current_issue in the database and
    extracts its title, returning a list of ``{"name", "issue_id", "issue_title"}``
    dicts suitable for ``workers`` fields in status / global-status responses.
    """
    result = []
    for agent in agents:
        issue_title = ""
        if agent.get("current_issue"):
            issue_row = db.get_issue(agent["current_issue"])
            if issue_row:
                issue_title = issue_row.get("title", "")
        result.append(
            {
                "name": agent.get("name", "") or "",
                "issue_id": agent.get("current_issue") or "",
                "issue_title": issue_title,
            }
        )
    return result
