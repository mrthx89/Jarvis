"""Final-answer mirroring without model calls or physical hardware."""
import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest

from nexo_server.answer_mirror import AnswerMirror
from nexo_server.session_hub import SessionHub
from tests.test_voice_reception import make_session
from nexo_server.models.messages import TranscriptionMessage


@pytest.mark.asyncio
async def test_speak_text_sends_full_final_subtitle_before_audio():
    device = make_session()
    device.ECHO_GUARD_SECONDS = 0
    try:
        await device.speak_text("Full answer", expression="netral")
        assert device.websocket.messages[0] == TranscriptionMessage(
            role="assistant", text="Full answer", is_final=True,
        ).model_dump(mode="json")
    finally:
        await device.close()


class Reader:
    def __init__(self):
        self.rows = []
        self.history = []
        self.cursor = 0

    def initialize(self):
        self.rows.clear()

    def high_water(self):
        return max((row["id"] for row in self.history + self.rows), default=0)

    def last_matching_final(self, text, after, through):
        return next((row["id"] for row in reversed(self.history + self.rows)
                     if after < row["id"] <= through and row["text"].strip() == text.strip()), None)

    def read_new(self):
        rows, self.rows = self.rows, []
        self.history.extend(rows)
        if rows:
            self.cursor = rows[-1]["id"]
        return rows


def setup_mirror():
    reader = Reader()
    hub = SessionHub()
    bridge = SimpleNamespace(query_active=0)
    mirror = AnswerMirror(reader, hub, bridge)
    return mirror, reader, hub, bridge


@pytest.mark.asyncio
async def test_terminal_final_reaches_subtitle_and_audio_once():
    mirror, reader, hub, bridge = setup_mirror()
    device = make_session()
    device.ECHO_GUARD_SECONDS = 0
    hub.register_session(device)
    reader.rows = [{"id": 1, "text": "Jawaban terminal"}]
    try:
        await mirror.poll_once()
        await mirror.deliver_once()
        await mirror.poll_once()
        await mirror.deliver_once()
        transcripts = [m for m in device.websocket.messages if m["type"] == "transcription"]
        assert [m["text"] for m in transcripts] == ["Jawaban terminal"]
        assert transcripts[0]["role"] == "assistant"
        assert sum(m["type"] == "tts_start" for m in device.websocket.messages) == 1
        assert hub.get_transcript()[-1]["source"] == "terminal_mirror"
    finally:
        await device.close()


@pytest.mark.asyncio
async def test_bridge_reply_suppressed_without_losing_concurrent_terminal_answer():
    mirror, reader, hub, bridge = setup_mirror()
    bridge.query_active = 1
    reader.rows = [{"id": 1, "text": "Terminal"}, {"id": 2, "text": "Voice"}]
    await mirror.poll_once()
    assert reader.rows
    await mirror.on_bridge_reply("Voice")
    bridge.query_active = 0
    assert [row["text"] for row in mirror.pending] == ["Terminal"]


@pytest.mark.asyncio
async def test_busy_device_defers_and_disconnected_device_drops_pending():
    mirror, reader, hub, bridge = setup_mirror()
    device = make_session()
    hub.register_session(device)
    device.is_recording = True
    reader.rows = [{"id": 1, "text": "Tunggu"}]
    try:
        await mirror.poll_once()
        await mirror.deliver_once()
        assert len(mirror.pending) == 1
        assert not device.websocket.messages
        hub.unregister_session(device)
        await mirror.deliver_once()
        assert not mirror.pending
    finally:
        await device.close()


@pytest.mark.asyncio
async def test_rapid_reconnect_drops_queued_reply_without_an_offline_tick():
    mirror, reader, hub, bridge = setup_mirror()
    device = make_session()
    hub.register_session(device)
    reader.rows = [{"id": 1, "text": "Before STOP"}]
    try:
        await mirror.poll_once()
        assert mirror.pending
        hub.unregister_session(device)
        assert not mirror.pending  # Lifecycle invalidation is synchronous.
        hub.register_session(device)
        await mirror.deliver_once()
        assert not cast(Any, device.websocket).messages
    finally:
        await device.close()


@pytest.mark.asyncio
async def test_reconnect_during_delivery_cannot_remove_new_session_reply():
    mirror, reader, hub, bridge = setup_mirror()
    entered, release = asyncio.Event(), asyncio.Event()

    async def paused_speech(*args, **kwargs):
        entered.set()
        await release.wait()
        return True

    device = SimpleNamespace(session_id="old", ready=True, speak_text=paused_speech)
    hub.register_session(device)
    reader.rows = [{"id": 1, "text": "Old"}]
    await mirror.poll_once()
    task = asyncio.create_task(mirror.deliver_once())
    try:
        await entered.wait()
        hub.unregister_session(device)
        hub.register_session(SimpleNamespace(session_id="new", ready=True))
        reader.rows = [{"id": 2, "text": "Fresh"}]
        await mirror.poll_once()
        release.set()
        await task
        assert [row["text"] for row in mirror.pending] == ["Fresh"]
        assert not hub.get_transcript()
    finally:
        release.set()
        await task


@pytest.mark.asyncio
async def test_expired_answer_is_not_spoken_later():
    mirror, reader, hub, bridge = setup_mirror()
    reader.rows = [{"id": 1, "text": "Lama"}]
    await mirror.poll_once()
    mirror.pending[0]["queued_at"] -= 121
    await mirror.deliver_once()
    assert not mirror.pending


@pytest.mark.asyncio
async def test_poll_racing_bridge_defers_voice_final_until_suppression():
    mirror, reader, hub, bridge = setup_mirror()

    read_new = reader.read_new
    reader.rows = [{"id": 1, "text": "Voice"}]

    def racing_read():
        bridge.query_active = 1
        return read_new()

    reader.read_new = racing_read
    await mirror.poll_once()
    assert not mirror.pending
    reader.read_new = lambda: []
    await mirror.on_bridge_reply("Voice")
    assert not mirror.pending


@pytest.mark.asyncio
async def test_worker_can_be_cancelled_cleanly():
    mirror, reader, hub, bridge = setup_mirror()
    task = asyncio.create_task(mirror.run())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
