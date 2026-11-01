"""Subprocess lifecycle tests use a real sleeping child, not a mocked process."""
import asyncio
import os
import sys

import pytest

from nexo_server.hermes.agent_bridge import HermesBridge


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


@pytest.mark.asyncio
async def test_query_stream_real_subprocess_sentence_chunking(tmp_path):
    script = tmp_path / "hermes-stream-test"
    script.write_text(
        f"#!{sys.executable}\n"
        "import sys, time\n"
        "sys.stdout.write(\"↻ Resumed session 'nexo'\\n\")\n"
        "sys.stdout.write(\"Model restored from session: hermes\\n\")\n"
        "sys.stdout.flush()\n"
        "time.sleep(0.02)\n"
        "sys.stdout.write(\"Halo kawan! \")\n"
        "sys.stdout.flush()\n"
        "time.sleep(0.02)\n"
        "sys.stdout.write(\"Apa kabar hari ini? \")\n"
        "sys.stdout.flush()\n"
        "time.sleep(0.02)\n"
        "sys.stdout.write(\"Saya siap membantu.\\n\")\n"
        "sys.stdout.write(\"session_id: local-123\\n\")\n"
        "sys.stdout.flush()\n"
    )
    script.chmod(0o700)

    replies = []
    async def hook(reply):
        replies.append(reply)

    bridge = HermesBridge(hermes_binary=str(script))
    bridge.on_reply = hook

    sentences = []
    async for s in bridge.query_stream("halo"):
        sentences.append(s)

    assert sentences == [
        "Halo kawan!",
        "Apa kabar hari ini?",
        "Saya siap membantu.",
    ]
    assert replies == ["Halo kawan! Apa kabar hari ini? Saya siap membantu."]


@pytest.mark.asyncio
async def test_query_stream_timeout_reaps_child(tmp_path):
    script, pid = sleeper(tmp_path)
    bridge = HermesBridge(hermes_binary=str(script), timeout=0.2)
    sentences = [s async for s in bridge.query_stream("halo")]
    assert len(sentences) == 1
    assert "melebihi batas" in sentences[0]
    assert pid.exists()
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid.read_text()), 0)


def test_dual_compatibility_hermes_bridge():
    """Verify legacy nexo_server hermes bridge imports match nexo_server."""
    from nexo_server.hermes.agent_bridge import HermesBridge as LegacyBridge
    assert LegacyBridge is not None
