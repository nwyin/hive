"""Tests for _terminate_process_group helper."""

import asyncio
import signal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hive.backends.base import _terminate_process_group


def _make_proc(returncode=None):
    proc = MagicMock()
    proc.pid = 1234
    proc.returncode = returncode
    proc.kill = MagicMock()
    return proc


@pytest.mark.asyncio
async def test_graceful_exit_sends_sigterm_then_returns():
    """Process exits after SIGTERM — no SIGKILL needed."""
    proc = _make_proc(returncode=None)

    async def wait():
        proc.returncode = 0

    proc.wait = wait

    with patch("os.killpg") as mock_killpg:
        await _terminate_process_group(proc, timeout=5)

    mock_killpg.assert_called_once_with(1234, signal.SIGTERM)
    proc.kill.assert_not_called()


@pytest.mark.asyncio
async def test_timeout_escalates_to_sigkill():
    """Process ignores SIGTERM — helper escalates to SIGKILL."""
    proc = _make_proc(returncode=None)

    async def wait_forever():
        await asyncio.sleep(100)

    proc.wait = wait_forever

    kill_calls = []

    def mock_killpg(pgid, sig):
        kill_calls.append(sig)

    with patch("os.killpg", side_effect=mock_killpg):
        await _terminate_process_group(proc, timeout=0.05)

    assert signal.SIGTERM in kill_calls
    assert signal.SIGKILL in kill_calls


@pytest.mark.asyncio
async def test_timeout_zero_skips_sigterm():
    """timeout=0 goes straight to SIGKILL without SIGTERM."""
    proc = _make_proc(returncode=None)
    proc.wait = AsyncMock()

    kill_calls = []

    def mock_killpg(pgid, sig):
        kill_calls.append(sig)

    with patch("os.killpg", side_effect=mock_killpg):
        await _terminate_process_group(proc, timeout=0)

    assert signal.SIGTERM not in kill_calls
    assert signal.SIGKILL in kill_calls


@pytest.mark.asyncio
async def test_timeout_zero_falls_back_to_proc_kill_when_still_running():
    """timeout=0 calls proc.kill() if returncode is still None after SIGKILL."""
    proc = _make_proc(returncode=None)
    proc.wait = AsyncMock()

    with patch("os.killpg"):
        await _terminate_process_group(proc, timeout=0)

    proc.kill.assert_called_once()


@pytest.mark.asyncio
async def test_proc_kill_skipped_if_already_exited():
    """proc.kill() is skipped if process has already exited after SIGKILL."""
    proc = _make_proc(returncode=None)

    def mock_killpg(pgid, sig):
        if sig == signal.SIGKILL:
            proc.returncode = -9

    proc.wait = AsyncMock()

    with patch("os.killpg", side_effect=mock_killpg):
        await _terminate_process_group(proc, timeout=0)

    proc.kill.assert_not_called()


@pytest.mark.asyncio
async def test_oserror_on_sigterm_is_suppressed():
    """OSError from killpg on SIGTERM does not propagate."""
    proc = _make_proc(returncode=None)

    async def wait():
        proc.returncode = 0

    proc.wait = wait

    with patch("os.killpg", side_effect=OSError("no such process")):
        # Should not raise
        await _terminate_process_group(proc, timeout=5)


@pytest.mark.asyncio
async def test_oserror_on_sigkill_is_suppressed():
    """OSError from killpg on SIGKILL does not propagate."""
    proc = _make_proc(returncode=None)
    proc.wait = AsyncMock(side_effect=asyncio.TimeoutError)
    proc.kill = MagicMock(side_effect=OSError)

    with patch("os.killpg", side_effect=OSError("no such process")):
        # Should not raise
        await _terminate_process_group(proc, timeout=0.01)
