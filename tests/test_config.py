"""Tests for config surface: HIVE_MERGE_POLICY invariants."""

import pytest

from hive.config import _Config, CANONICAL_MERGE_POLICIES


# ── INV-1: default resolved policy is mechanical_then_refinery ───────────────


def test_default_merge_policy_is_mechanical_then_refinery():
    """INV-1: clean startup with no env vars uses default policy."""
    cfg = _Config()
    assert cfg.MERGE_POLICY == "mechanical_then_refinery"


def test_default_merge_policy_not_merge_queue_enabled(monkeypatch):
    """INV-1: merge_queue_enabled is not a recognized config field."""
    cfg = _Config()
    assert not hasattr(cfg, "MERGE_QUEUE_ENABLED"), "merge_queue_enabled must not exist on Config"


# ── INV-2: invalid policy value is rejected during config resolution ──────────


def test_invalid_env_policy_rejected(monkeypatch):
    """INV-2: invalid HIVE_MERGE_POLICY value raises ValueError during config init."""
    monkeypatch.setenv("HIVE_MERGE_POLICY", "auto")
    with pytest.raises(ValueError, match="HIVE_MERGE_POLICY"):
        _Config()


@pytest.mark.parametrize("invalid", ["true", "false", "1", "enabled", "queue"])
def test_invalid_env_policy_various_bad_values(monkeypatch, invalid):
    """INV-2: all non-canonical values are rejected."""
    monkeypatch.setenv("HIVE_MERGE_POLICY", invalid)
    with pytest.raises(ValueError, match="Invalid merge policy"):
        _Config()


# ── INV-3: env override with valid value changes effective policy ─────────────


@pytest.mark.parametrize("policy", CANONICAL_MERGE_POLICIES)
def test_env_override_with_valid_policy(monkeypatch, policy):
    """Valid HIVE_MERGE_POLICY env var is accepted and reflected in Config."""
    monkeypatch.setenv("HIVE_MERGE_POLICY", policy)
    cfg = _Config()
    assert cfg.MERGE_POLICY == policy


def test_env_override_refinery_first(monkeypatch):
    """Env override with refinery_first changes effective policy."""
    monkeypatch.setenv("HIVE_MERGE_POLICY", "refinery_first")
    cfg = _Config()
    assert cfg.MERGE_POLICY == "refinery_first"


def test_env_override_manual(monkeypatch):
    """Env override with manual changes effective policy."""
    monkeypatch.setenv("HIVE_MERGE_POLICY", "manual")
    cfg = _Config()
    assert cfg.MERGE_POLICY == "manual"
