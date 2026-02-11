"""pytest fixtures for Hive tests."""

import os
import tempfile
from pathlib import Path

import pytest

from hive.db import Database


@pytest.fixture
def temp_db():
    """Provide a temporary test database."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    db = Database(db_path)
    db.connect()

    yield db

    db.close()
    os.unlink(db_path)


@pytest.fixture
def db_with_issues(temp_db):
    """Provide a database pre-populated with test issues."""
    # Create some test issues
    issue1 = temp_db.create_issue("Task 1", "First task", priority=1, project="test")
    issue2 = temp_db.create_issue("Task 2", "Second task", priority=2, project="test")
    issue3 = temp_db.create_issue("Task 3", "Blocked task", priority=1, project="test")

    # Add dependency: issue3 depends on issue1
    temp_db.add_dependency(issue3, issue1)

    return temp_db, {"issue1": issue1, "issue2": issue2, "issue3": issue3}
