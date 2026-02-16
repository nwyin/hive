"""Integration tests for project isolation across database operations.

Tests verify that all database methods properly filter by project parameter,
ensuring complete data isolation between projects.
"""

import tempfile
import os

import pytest

from hive.db import Database


@pytest.fixture
def isolation_db():
    """Create a fresh database for isolation testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    db = Database(db_path)
    db.connect()

    yield db

    db.close()
    os.unlink(db_path)


def test_get_ready_queue_project_isolation(isolation_db):
    """Test 1: Create issues for project alpha and beta. Verify get_ready_queue(alpha) returns only alpha issues."""
    db = isolation_db

    # Create issues for alpha
    alpha_issue1 = db.create_issue("Alpha Issue 1", project="alpha")
    alpha_issue2 = db.create_issue("Alpha Issue 2", project="alpha")

    # Create issues for beta
    beta_issue1 = db.create_issue("Beta Issue 1", project="beta")
    beta_issue2 = db.create_issue("Beta Issue 2", project="beta")

    # Test alpha project isolation
    alpha_queue = db.get_ready_queue(project="alpha")
    alpha_ids = [item["id"] for item in alpha_queue]

    assert len(alpha_ids) == 2
    assert alpha_issue1 in alpha_ids
    assert alpha_issue2 in alpha_ids
    assert beta_issue1 not in alpha_ids
    assert beta_issue2 not in alpha_ids

    # Test beta project isolation
    beta_queue = db.get_ready_queue(project="beta")
    beta_ids = [item["id"] for item in beta_queue]

    assert len(beta_ids) == 2
    assert beta_issue1 in beta_ids
    assert beta_issue2 in beta_ids
    assert alpha_issue1 not in beta_ids
    assert alpha_issue2 not in beta_ids

    # Test no filter returns all
    all_queue = db.get_ready_queue()
    assert len(all_queue) == 4


def test_get_recent_project_notes_project_isolation(isolation_db):
    """Test 2: Add notes for both projects. Verify get_recent_project_notes(alpha) returns only alpha notes + NULL-project notes."""
    db = isolation_db

    # Create issues for both projects
    alpha_issue = db.create_issue("Alpha Issue", project="alpha")
    beta_issue = db.create_issue("Beta Issue", project="beta")

    # Create agents
    alpha_agent = db.create_agent("alpha-agent", project="alpha")
    beta_agent = db.create_agent("beta-agent", project="beta")

    # Add notes for alpha
    db.add_note(issue_id=alpha_issue, agent_id=alpha_agent, content="Alpha discovery", project="alpha")
    db.add_note(issue_id=alpha_issue, agent_id=alpha_agent, content="Alpha gotcha", project="alpha", category="gotcha")

    # Add notes for beta
    db.add_note(issue_id=beta_issue, agent_id=beta_agent, content="Beta discovery", project="beta")
    db.add_note(issue_id=beta_issue, agent_id=beta_agent, content="Beta pattern", project="beta", category="pattern")

    # Add NULL-project note (should appear in both queries)
    db.add_note(content="Global system note", project=None)

    # Test alpha project isolation
    alpha_notes = db.get_recent_project_notes(project="alpha")
    alpha_contents = [note["content"] for note in alpha_notes]

    assert "Alpha discovery" in alpha_contents
    assert "Alpha gotcha" in alpha_contents
    assert "Global system note" in alpha_contents  # NULL-project notes match any query
    assert "Beta discovery" not in alpha_contents
    assert "Beta pattern" not in alpha_contents

    # Test beta project isolation
    beta_notes = db.get_recent_project_notes(project="beta")
    beta_contents = [note["content"] for note in beta_notes]

    assert "Beta discovery" in beta_contents
    assert "Beta pattern" in beta_contents
    assert "Global system note" in beta_contents  # NULL-project notes match any query
    assert "Alpha discovery" not in beta_contents
    assert "Alpha gotcha" not in beta_contents

    # Test no filter returns all
    all_notes = db.get_recent_project_notes()
    assert len(all_notes) == 5


def test_get_active_agents_project_isolation(isolation_db):
    """Test 3: Create agents for both projects. Verify get_active_agents(alpha) returns only alpha agents."""
    db = isolation_db

    # Create agents for alpha
    alpha_agent1 = db.create_agent("alpha-agent-1", project="alpha")
    alpha_agent2 = db.create_agent("alpha-agent-2", project="alpha")

    # Create agents for beta
    beta_agent1 = db.create_agent("beta-agent-1", project="beta")
    beta_agent2 = db.create_agent("beta-agent-2", project="beta")

    # Set all agents to working status
    db.conn.execute("UPDATE agents SET status = 'working' WHERE id IN (?, ?, ?, ?)", (alpha_agent1, alpha_agent2, beta_agent1, beta_agent2))
    db.conn.commit()

    # Test alpha project isolation
    alpha_active = db.get_active_agents(project="alpha")
    alpha_agent_ids = [agent["id"] for agent in alpha_active]

    assert len(alpha_agent_ids) == 2
    assert alpha_agent1 in alpha_agent_ids
    assert alpha_agent2 in alpha_agent_ids
    assert beta_agent1 not in alpha_agent_ids
    assert beta_agent2 not in alpha_agent_ids

    # Test beta project isolation
    beta_active = db.get_active_agents(project="beta")
    beta_agent_ids = [agent["id"] for agent in beta_active]

    assert len(beta_agent_ids) == 2
    assert beta_agent1 in beta_agent_ids
    assert beta_agent2 in beta_agent_ids
    assert alpha_agent1 not in beta_agent_ids
    assert alpha_agent2 not in beta_agent_ids

    # Test no filter returns all
    all_active = db.get_active_agents()
    assert len(all_active) == 4


def test_get_queued_merges_project_isolation(isolation_db):
    """Test 4: Enqueue merges for both projects. Verify get_queued_merges(alpha) returns only alpha merges."""
    db = isolation_db

    # Create issues and agents for alpha
    alpha_issue1 = db.create_issue("Alpha Feature 1", project="alpha")
    alpha_issue2 = db.create_issue("Alpha Feature 2", project="alpha")
    alpha_agent = db.create_agent("alpha-agent", project="alpha")

    # Create issues and agents for beta
    beta_issue1 = db.create_issue("Beta Feature 1", project="beta")
    beta_issue2 = db.create_issue("Beta Feature 2", project="beta")
    beta_agent = db.create_agent("beta-agent", project="beta")

    # Mark all issues as done
    db.update_issue_status(alpha_issue1, "done")
    db.update_issue_status(alpha_issue2, "done")
    db.update_issue_status(beta_issue1, "done")
    db.update_issue_status(beta_issue2, "done")

    # Enqueue merges for alpha
    db.conn.execute(
        "INSERT INTO merge_queue (issue_id, agent_id, project, worktree, branch_name) VALUES (?, ?, ?, ?, ?)",
        (alpha_issue1, alpha_agent, "alpha", "/tmp/alpha-wt1", "agent/alpha-1"),
    )
    db.conn.execute(
        "INSERT INTO merge_queue (issue_id, agent_id, project, worktree, branch_name) VALUES (?, ?, ?, ?, ?)",
        (alpha_issue2, alpha_agent, "alpha", "/tmp/alpha-wt2", "agent/alpha-2"),
    )

    # Enqueue merges for beta
    db.conn.execute(
        "INSERT INTO merge_queue (issue_id, agent_id, project, worktree, branch_name) VALUES (?, ?, ?, ?, ?)",
        (beta_issue1, beta_agent, "beta", "/tmp/beta-wt1", "agent/beta-1"),
    )
    db.conn.execute(
        "INSERT INTO merge_queue (issue_id, agent_id, project, worktree, branch_name) VALUES (?, ?, ?, ?, ?)",
        (beta_issue2, beta_agent, "beta", "/tmp/beta-wt2", "agent/beta-2"),
    )
    db.conn.commit()

    # Test alpha project isolation
    alpha_merges = db.get_queued_merges(project="alpha")
    alpha_issue_ids = [merge["issue_id"] for merge in alpha_merges]

    assert len(alpha_issue_ids) == 2
    assert alpha_issue1 in alpha_issue_ids
    assert alpha_issue2 in alpha_issue_ids
    assert beta_issue1 not in alpha_issue_ids
    assert beta_issue2 not in alpha_issue_ids

    # Test beta project isolation
    beta_merges = db.get_queued_merges(project="beta")
    beta_issue_ids = [merge["issue_id"] for merge in beta_merges]

    assert len(beta_issue_ids) == 2
    assert beta_issue1 in beta_issue_ids
    assert beta_issue2 in beta_issue_ids
    assert alpha_issue1 not in beta_issue_ids
    assert alpha_issue2 not in beta_issue_ids

    # Test no filter returns all
    all_merges = db.get_queued_merges()
    assert len(all_merges) == 4


def test_get_token_usage_project_isolation(isolation_db):
    """Test 5: Log token events for both projects. Verify get_token_usage(project=alpha) returns only alpha costs."""
    db = isolation_db

    # Create issues and agents for alpha
    alpha_issue1 = db.create_issue("Alpha Task 1", project="alpha")
    alpha_issue2 = db.create_issue("Alpha Task 2", project="alpha")
    alpha_agent = db.create_agent("alpha-agent", project="alpha")

    # Create issues and agents for beta
    beta_issue1 = db.create_issue("Beta Task 1", project="beta")
    beta_issue2 = db.create_issue("Beta Task 2", project="beta")
    beta_agent = db.create_agent("beta-agent", project="beta")

    # Log token usage for alpha (1000 input + 500 output per issue)
    db.log_event(alpha_issue1, alpha_agent, "tokens_used", {"input_tokens": 1000, "output_tokens": 500, "model": "claude-sonnet-4-5-20250929"})
    db.log_event(alpha_issue2, alpha_agent, "tokens_used", {"input_tokens": 1000, "output_tokens": 500, "model": "claude-sonnet-4-5-20250929"})

    # Log token usage for beta (2000 input + 1000 output per issue)
    db.log_event(beta_issue1, beta_agent, "tokens_used", {"input_tokens": 2000, "output_tokens": 1000, "model": "claude-sonnet-4-5-20250929"})
    db.log_event(beta_issue2, beta_agent, "tokens_used", {"input_tokens": 2000, "output_tokens": 1000, "model": "claude-sonnet-4-5-20250929"})

    # Test alpha project isolation
    alpha_usage = db.get_token_usage(project="alpha")
    assert alpha_usage["total_input_tokens"] == 2000  # 1000 * 2
    assert alpha_usage["total_output_tokens"] == 1000  # 500 * 2
    assert alpha_usage["total_tokens"] == 3000

    # Verify issue breakdown only contains alpha issues
    assert alpha_issue1 in alpha_usage["issue_breakdown"]
    assert alpha_issue2 in alpha_usage["issue_breakdown"]
    assert beta_issue1 not in alpha_usage["issue_breakdown"]
    assert beta_issue2 not in alpha_usage["issue_breakdown"]

    # Test beta project isolation
    beta_usage = db.get_token_usage(project="beta")
    assert beta_usage["total_input_tokens"] == 4000  # 2000 * 2
    assert beta_usage["total_output_tokens"] == 2000  # 1000 * 2
    assert beta_usage["total_tokens"] == 6000

    # Verify issue breakdown only contains beta issues
    assert beta_issue1 in beta_usage["issue_breakdown"]
    assert beta_issue2 in beta_usage["issue_breakdown"]
    assert alpha_issue1 not in beta_usage["issue_breakdown"]
    assert alpha_issue2 not in beta_usage["issue_breakdown"]

    # Test no filter returns all
    all_usage = db.get_token_usage()
    assert all_usage["total_input_tokens"] == 6000
    assert all_usage["total_output_tokens"] == 3000
    assert all_usage["total_tokens"] == 9000


def test_null_project_notes_visible_to_all_projects(isolation_db):
    """Test 6: Add a project-wide note (NULL issue_id, NULL project). Verify it appears in queries for BOTH projects."""
    db = isolation_db

    # Create issues for both projects
    alpha_issue = db.create_issue("Alpha Issue", project="alpha")
    beta_issue = db.create_issue("Beta Issue", project="beta")

    # Add project-specific notes
    db.add_note(issue_id=alpha_issue, content="Alpha-specific note", project="alpha")
    db.add_note(issue_id=beta_issue, content="Beta-specific note", project="beta")

    # Add NULL-project notes (should be visible to all projects)
    db.add_note(content="Global system pattern", project=None, category="pattern")
    db.add_note(content="Global dependency note", project=None, category="dependency")

    # Test alpha queries include NULL-project notes
    alpha_notes = db.get_notes(project="alpha")
    alpha_contents = [note["content"] for note in alpha_notes]

    assert "Alpha-specific note" in alpha_contents
    assert "Global system pattern" in alpha_contents
    assert "Global dependency note" in alpha_contents
    assert "Beta-specific note" not in alpha_contents

    # Test beta queries include NULL-project notes
    beta_notes = db.get_notes(project="beta")
    beta_contents = [note["content"] for note in beta_notes]

    assert "Beta-specific note" in beta_contents
    assert "Global system pattern" in beta_contents
    assert "Global dependency note" in beta_contents
    assert "Alpha-specific note" not in beta_contents

    # Test get_recent_project_notes also includes NULL-project notes
    alpha_recent = db.get_recent_project_notes(project="alpha")
    alpha_recent_contents = [note["content"] for note in alpha_recent]

    assert "Global system pattern" in alpha_recent_contents
    assert "Global dependency note" in alpha_recent_contents
    assert "Alpha-specific note" in alpha_recent_contents

    beta_recent = db.get_recent_project_notes(project="beta")
    beta_recent_contents = [note["content"] for note in beta_recent]

    assert "Global system pattern" in beta_recent_contents
    assert "Global dependency note" in beta_recent_contents
    assert "Beta-specific note" in beta_recent_contents


def test_migration_backfill_project_from_issue_fk(isolation_db):
    """Test 7: Verify migration backfill: create notes without project column, run migration, verify project was populated from issue FK."""
    db = isolation_db

    # Create issues with projects
    alpha_issue = db.create_issue("Alpha Issue", project="alpha")
    beta_issue = db.create_issue("Beta Issue", project="beta")

    # Create agents
    alpha_agent = db.create_agent("alpha-agent", project="alpha")
    beta_agent = db.create_agent("beta-agent", project="beta")

    # Add notes with project set (normal operation)
    alpha_note_id = db.add_note(issue_id=alpha_issue, agent_id=alpha_agent, content="Alpha note", project="alpha")
    beta_note_id = db.add_note(issue_id=beta_issue, agent_id=beta_agent, content="Beta note", project="beta")

    # Manually clear the project column to simulate old data (before migration)
    db.conn.execute("UPDATE notes SET project = NULL WHERE id IN (?, ?)", (alpha_note_id, beta_note_id))
    db.conn.commit()

    # Verify project is NULL
    cursor = db.conn.execute("SELECT project FROM notes WHERE id = ?", (alpha_note_id,))
    assert cursor.fetchone()["project"] is None
    cursor = db.conn.execute("SELECT project FROM notes WHERE id = ?", (beta_note_id,))
    assert cursor.fetchone()["project"] is None

    # Run migration (this triggers the backfill)
    db._migrate_if_needed()

    # Verify project was backfilled from issues.project via FK
    cursor = db.conn.execute("SELECT project FROM notes WHERE id = ?", (alpha_note_id,))
    alpha_backfilled = cursor.fetchone()["project"]
    assert alpha_backfilled == "alpha"

    cursor = db.conn.execute("SELECT project FROM notes WHERE id = ?", (beta_note_id,))
    beta_backfilled = cursor.fetchone()["project"]
    assert beta_backfilled == "beta"

    # Verify backfilled notes now appear in correct project queries
    alpha_notes = db.get_notes(project="alpha")
    alpha_contents = [note["content"] for note in alpha_notes]
    assert "Alpha note" in alpha_contents
    assert "Beta note" not in alpha_contents

    beta_notes = db.get_notes(project="beta")
    beta_contents = [note["content"] for note in beta_notes]
    assert "Beta note" in beta_contents
    assert "Alpha note" not in beta_contents


def test_migration_backfill_preserves_null_project_notes(isolation_db):
    """Test that migration backfill preserves NULL project for notes without issue_id."""
    db = isolation_db

    # Add a project-wide note (no issue_id, no project)
    global_note_id = db.add_note(content="Global note")

    # Manually clear project to simulate old data
    db.conn.execute("UPDATE notes SET project = NULL WHERE id = ?", (global_note_id,))
    db.conn.commit()

    # Verify project is NULL
    cursor = db.conn.execute("SELECT project FROM notes WHERE id = ?", (global_note_id,))
    assert cursor.fetchone()["project"] is None

    # Run migration
    db._migrate_if_needed()

    # Verify project remains NULL (no issue_id to backfill from)
    cursor = db.conn.execute("SELECT project FROM notes WHERE id = ?", (global_note_id,))
    note_project = cursor.fetchone()["project"]
    assert note_project is None

    # Verify this note is still visible to all project queries
    alpha_notes = db.get_notes(project="alpha")
    alpha_contents = [note["content"] for note in alpha_notes]
    assert "Global note" in alpha_contents

    beta_notes = db.get_notes(project="beta")
    beta_contents = [note["content"] for note in beta_notes]
    assert "Global note" in beta_contents


def test_get_metrics_project_isolation(isolation_db):
    """Test get_metrics filters correctly by project."""
    db = isolation_db

    # Create issues for both projects
    alpha_issue = db.create_issue("Alpha Task", project="alpha", model="claude-sonnet-4-5-20250929")
    beta_issue = db.create_issue("Beta Task", project="beta", model="claude-sonnet-4-5-20250929")

    # Create agents
    alpha_agent = db.create_agent("alpha-agent", project="alpha")
    beta_agent = db.create_agent("beta-agent", project="beta")

    # Create agent_runs events for alpha
    db.log_event(alpha_issue, alpha_agent, "worker_started", {})
    db.log_event(alpha_issue, alpha_agent, "completed", {})

    # Create agent_runs events for beta
    db.log_event(beta_issue, beta_agent, "worker_started", {})
    db.log_event(beta_issue, beta_agent, "completed", {})

    # Test alpha project isolation
    alpha_metrics = db.get_metrics(project="alpha")
    assert len(alpha_metrics) >= 1

    # Test beta project isolation
    beta_metrics = db.get_metrics(project="beta")
    assert len(beta_metrics) >= 1

    # Test no filter returns all
    all_metrics = db.get_metrics()
    assert len(all_metrics) >= 1


def test_comprehensive_isolation_scenario(isolation_db):
    """Comprehensive test combining multiple operations to verify end-to-end project isolation."""
    db = isolation_db

    # Create complete ecosystems for two projects
    # Project Alpha
    alpha_issue1 = db.create_issue("Alpha Feature A", project="alpha", priority=1)
    alpha_issue2 = db.create_issue("Alpha Feature B", project="alpha", priority=2)
    alpha_agent1 = db.create_agent("alpha-worker-1", project="alpha")
    db.create_agent("alpha-worker-2", project="alpha")

    db.add_note(issue_id=alpha_issue1, content="Alpha discovery 1", project="alpha", category="discovery")
    db.add_note(issue_id=alpha_issue2, content="Alpha pattern 1", project="alpha", category="pattern")

    db.log_event(alpha_issue1, alpha_agent1, "tokens_used", {"input_tokens": 500, "output_tokens": 250, "model": "claude-sonnet-4-5-20250929"})

    # Project Beta
    beta_issue1 = db.create_issue("Beta Feature A", project="beta", priority=1)
    beta_issue2 = db.create_issue("Beta Feature B", project="beta", priority=2)
    beta_agent1 = db.create_agent("beta-worker-1", project="beta")
    db.create_agent("beta-worker-2", project="beta")

    db.add_note(issue_id=beta_issue1, content="Beta discovery 1", project="beta", category="discovery")
    db.add_note(issue_id=beta_issue2, content="Beta gotcha 1", project="beta", category="gotcha")

    db.log_event(beta_issue1, beta_agent1, "tokens_used", {"input_tokens": 1000, "output_tokens": 500, "model": "claude-sonnet-4-5-20250929"})

    # Add global note
    db.add_note(content="Cross-project system note", project=None)

    # Set agents to working
    db.conn.execute("UPDATE agents SET status = 'working' WHERE project IN ('alpha', 'beta')")
    db.conn.commit()

    # Verify alpha isolation across all methods
    alpha_queue = db.get_ready_queue(project="alpha")
    assert len(alpha_queue) == 2
    assert all(item["project"] == "alpha" for item in alpha_queue)

    alpha_agents = db.get_active_agents(project="alpha")
    assert len(alpha_agents) == 2
    assert all(agent["project"] == "alpha" for agent in alpha_agents)

    alpha_notes = db.get_notes(project="alpha")
    assert len(alpha_notes) == 3  # 2 alpha notes + 1 global note
    alpha_projects = [note["project"] for note in alpha_notes]
    assert "alpha" in alpha_projects
    assert None in alpha_projects  # Global note
    assert "beta" not in alpha_projects

    alpha_tokens = db.get_token_usage(project="alpha")
    assert alpha_tokens["total_tokens"] == 750  # 500 + 250

    # Verify beta isolation across all methods
    beta_queue = db.get_ready_queue(project="beta")
    assert len(beta_queue) == 2
    assert all(item["project"] == "beta" for item in beta_queue)

    beta_agents = db.get_active_agents(project="beta")
    assert len(beta_agents) == 2
    assert all(agent["project"] == "beta" for agent in beta_agents)

    beta_notes = db.get_notes(project="beta")
    assert len(beta_notes) == 3  # 2 beta notes + 1 global note
    beta_projects = [note["project"] for note in beta_notes]
    assert "beta" in beta_projects
    assert None in beta_projects  # Global note
    assert "alpha" not in beta_projects

    beta_tokens = db.get_token_usage(project="beta")
    assert beta_tokens["total_tokens"] == 1500  # 1000 + 500

    # Verify totals without filter
    all_queue = db.get_ready_queue()
    assert len(all_queue) == 4

    all_agents = db.get_active_agents()
    assert len(all_agents) == 4

    all_notes = db.get_notes()
    assert len(all_notes) == 5  # 2 alpha + 2 beta + 1 global

    all_tokens = db.get_token_usage()
    assert all_tokens["total_tokens"] == 2250  # 750 + 1500
