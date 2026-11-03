"""Exercise the actual gateway adapter, not just the transport state machine."""
import asyncio
import hashlib
import json
import struct
import uuid
from unittest.mock import AsyncMock

import pytest

from nexo_server.gateway.websocket_handler import ApplianceSession, ProtocolViolation
from nexo_server.gateway.outdoor import OutdoorStore
from nexo_server.models.messages import NexoState, TTSStart


def uid():
    return str(uuid.uuid4())


class Socket:
    def __init__(self):
        self.frames = []

    async def send_text(self, text):
        self.frames.append(json.loads(text))

    async def send_bytes(self, data):
        self.frames.append(data)


async def connect(caps=True, store=None):
    socket = Socket()
    session = ApplianceSession(socket, uid(), AsyncMock(), AsyncMock(), AsyncMock(),
                               authenticated_device_id=uid())
    session.outdoor_store = store or OutdoorStore()
    hello = dict(type="client_hello", device_id=session.authenticated_device_id,
                 protocol_version="1.0", audio_spec=dict(format="audio/pcm", sample_rate=16000,
                                                         channels=1, bit_depth=16))
    if caps:
        hello["capabilities"] = ["utterance_upload_v2", "exclusive_audio_v1"]
    await session.handle_text_frame(json.dumps(hello))
    return session, socket


async def control(session, kind, **fields):
    p = dict(type=kind, connection_id=session.outdoor.connection_id, request_id=uid(), **fields)
    await session.handle_text_frame(json.dumps(p))
    return p


@pytest.mark.asyncio
async def test_gateway_negotiates_actual_android_hello_paused():
    s, ws = await connect()
    try:
        h = ws.frames[-1]
        assert h["protocol_version"] == "2.0"
        assert h["conversation_paused"] is True
        assert h["limits"]["max_samples"] == 320000
        assert s._busy()
        assert await s.speak_text("unsolicited") is False
        assert len(ws.frames) == 1
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_gateway_commits_whole_pcm_once_without_vad_or_arrival_gap():
    s, ws = await connect()
    try:
        await control(s, "conversation_resume")
        pcm = b"\x01\x02" * 2000 + b"\x00\x00" * 8000 + b"\x02\x03" * 2000
        u = uid()
        meta = dict(utterance_id=u, total_samples=len(pcm)//2,
                    sha256=hashlib.sha256(pcm).hexdigest())
        s._run_pipeline = AsyncMock(return_value="ok")
        s.vad.is_speech = lambda *a, **k: pytest.fail("v2 entered VAD")
        await control(s, "utterance_begin", purpose="conversation", end_reason="manual_finish", **meta)
        for offset in range(0, len(pcm), 8000):
            part = pcm[offset:offset+8000]
            await s.handle_binary_frame(struct.pack(">4s16sII", b"NXA2", uuid.UUID(u).bytes,
                                                     offset//2, len(part)) + part)
        s._on_audio_gap()
        s._run_pipeline.assert_not_called()
        await control(s, "utterance_commit", **meta)
        await s.outdoor.active_task
        s._run_pipeline.assert_awaited_once_with(pcm)
        await control(s, "utterance_commit", **meta)
        assert s._run_pipeline.await_count == 1
        assert any(f.get("type") == "utterance_result" and f["status"] == "completed"
                   for f in ws.frames if isinstance(f, dict))
    finally:
        await s.close()
