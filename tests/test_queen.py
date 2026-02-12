"""Tests for Queen Bee functionality (agent definition and CLI integration)."""

import os


def test_queen_agent_definition_exists():
    """Test that the queen bee agent definition file exists."""
    agent_file = os.path.join(os.path.dirname(__file__), "..", ".opencode", "agents", "queen.md")
    assert os.path.exists(agent_file), "Queen Bee agent definition missing at .opencode/agents/queen.md"


def test_queen_agent_definition_has_frontmatter():
    """Test that the queen bee agent definition has valid frontmatter."""
    agent_file = os.path.join(os.path.dirname(__file__), "..", ".opencode", "agents", "queen.md")
    with open(agent_file) as f:
        content = f.read()

    assert content.startswith("---"), "Agent definition must start with YAML frontmatter"
    # Find end of frontmatter
    end_idx = content.index("---", 3)
    frontmatter = content[3:end_idx].strip()

    import yaml

    meta = yaml.safe_load(frontmatter)
    assert "description" in meta
    assert meta.get("tools", {}).get("write") is True
    assert meta.get("tools", {}).get("edit") is True
    assert "permission" in meta


def test_queen_agent_definition_references_cli():
    """Test that the queen bee agent definition references hive CLI commands."""
    agent_file = os.path.join(os.path.dirname(__file__), "..", ".opencode", "agents", "queen.md")
    with open(agent_file) as f:
        content = f.read()

    # Should reference key CLI commands
    assert "hive create" in content
    assert "hive list" in content
    assert "hive status" in content
    assert "hive cancel" in content
    assert "hive finalize" in content
    assert "hive molecule" in content
    assert "hive dep add" in content
    assert "--json" in content
