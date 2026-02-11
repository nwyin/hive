"""Tests for ID generation."""

import re

from hive.ids import generate_id


def test_generate_id_format():
    """Test that generated IDs follow the expected format."""
    issue_id = generate_id("w")
    assert re.match(r"^w-[a-f0-9]{6}$", issue_id)

    agent_id = generate_id("agent")
    assert re.match(r"^agent-[a-f0-9]{6}$", agent_id)


def test_generate_id_uniqueness():
    """Test that generated IDs are unique."""
    ids = [generate_id("w") for _ in range(1000)]
    assert len(ids) == len(set(ids)), "Generated IDs should be unique"


def test_generate_id_default_prefix():
    """Test default prefix is 'w'."""
    issue_id = generate_id()
    assert issue_id.startswith("w-")


def test_generate_id_custom_prefix():
    """Test custom prefixes work correctly."""
    prefixes = ["task", "bug", "feature", "agent"]
    for prefix in prefixes:
        id_val = generate_id(prefix)
        assert id_val.startswith(f"{prefix}-")
        assert len(id_val) == len(prefix) + 7  # prefix + "-" + 6 hex chars
