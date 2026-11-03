"""Legacy mode cannot bypass a same-device v2 exclusive lease."""
import json
from unittest.mock import AsyncMock
import pytest
from nexo_server.gateway.websocket_handler import ApplianceSession, ProtocolViolation
from tests.test_outdoor_gateway import connect, Socket, uid
from nexo_server.gateway.outdoor import OutdoorStore


@pytest.mark.asyncio
@pytest.mark.parametrize("first_caps,second_caps", [(True, False), (False, True), (False, False)])
async def test_one_live_hello_per_device_across_protocol_versions(first_caps, second_caps):
    store = OutdoorStore()
    s, ws = await connect(caps=first_caps, store=store)
    other = ApplianceSession(Socket(), uid(), AsyncMock(), AsyncMock(), AsyncMock(),
                             authenticated_device_id=s.authenticated_device_id)
    other.outdoor_store = store
    hello = dict(type="client_hello", device_id=s.authenticated_device_id, protocol_version="1.0",
                 audio_spec=dict(format="audio/pcm", sample_rate=16000, channels=1, bit_depth=16))
    if second_caps:
        hello["capabilities"] = ["utterance_upload_v2", "exclusive_audio_v1"]
    try:
        with pytest.raises(ProtocolViolation):
            await other.handle_text_frame(json.dumps(hello))
    finally:
        await other.close()
        await s.close()
    assert s.authenticated_device_id not in store.connections
