"""Legacy disconnect must not free admission while providers still clean up."""
import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from nexo_server.gateway.websocket_handler import ApplianceSession, ProtocolViolation
from tests.test_outdoor_gateway import Socket, connect, uid


@pytest.mark.parametrize("bounded", [False, True])
@pytest.mark.asyncio
async def test_legacy_close_keeps_same_device_quarantined_until_cancellation_finishes(bounded):
    old, ws = await connect(caps=False)
    entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def provider():
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()
    if bounded:
        old.LEGACY_DRAIN_TIMEOUT = 0.01
    old._processing_task = asyncio.create_task(provider())
    await entered.wait()
    closing = asyncio.create_task(old.close())
    await cancelled.wait()
    new = ApplianceSession(Socket(), uid(), AsyncMock(), AsyncMock(), AsyncMock(),
                           authenticated_device_id=old.authenticated_device_id)
    new.outdoor_store = old.outdoor_store
    hello = dict(type="client_hello", protocol_version="1.0", device_id=old.authenticated_device_id,
                 audio_spec=dict(format="audio/pcm", sample_rate=16000, channels=1, bit_depth=16),
                 capabilities=["utterance_upload_v2", "exclusive_audio_v1"])
    try:
        if bounded:
            await asyncio.wait_for(asyncio.shield(closing), 0.2)
            assert not old._processing_task.done()
        with pytest.raises(ProtocolViolation, match="admission"):
            await new.handle_text_frame(json.dumps(hello))
        assert old.outdoor_store.connections[old.authenticated_device_id] is old
    finally:
        release.set()
        await closing
        await old._processing_task
        await new.close()
    replacement = ApplianceSession(Socket(), uid(), AsyncMock(), AsyncMock(), AsyncMock(),
                                   authenticated_device_id=old.authenticated_device_id)
    replacement.outdoor_store = old.outdoor_store
    try:
        await replacement.handle_text_frame(json.dumps(hello))
        assert replacement.ready
    finally:
        await replacement.close()
