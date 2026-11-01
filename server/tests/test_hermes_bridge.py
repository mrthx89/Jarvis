"""Subprocess lifecycle tests use a real sleeping child, not a mocked process."""
import asyncio
import os
import sys

import pytest

from jarvis_server.hermes.agent_bridge import HermesBridge


def sleeper(tmp_path):
    pid = tmp_path / "pid"
    script = tmp_path / "hermes-test"
    script.write_text(f"#!{sys.executable}\nimport os,time\nfrom pathlib import Path\nPath({str(pid)!r}).write_text(str(os.getpid()))\ntime.sleep(60)\n")
    script.chmod(0o700)
    return script, pid


@pytest.mark.asyncio
async def test_timeout_reaps_real_child(tmp_path):
    script, pid = sleeper(tmp_path)
    bridge = HermesBridge(hermes_binary=str(script), timeout=0.2)
    response = await bridge.query("halo")
    assert "melebihi batas" in response
    assert pid.exists()
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid.read_text()), 0)


@pytest.mark.asyncio
async def test_cancellation_reaps_real_child(tmp_path):
    script, pid = sleeper(tmp_path)
    bridge = HermesBridge(hermes_binary=str(script))
    task = asyncio.create_task(bridge.query("halo"))
    for _ in range(100):
        if pid.exists():
            break
        await asyncio.sleep(0.01)
    assert pid.exists()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid.read_text()), 0)
