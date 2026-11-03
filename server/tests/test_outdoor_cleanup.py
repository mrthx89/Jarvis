"""Cleanup must unregister hub even while device work is quarantined."""
from unittest.mock import AsyncMock, Mock
import pytest
from tests.test_outdoor_gateway import connect
from nexo_server.gateway.outdoor import OutdoorViolation


@pytest.mark.asyncio
async def test_close_unregisters_hub_even_if_runtime_drain_fails():
    s, ws = await connect()
    s.hub = Mock()
    s.outdoor.close = AsyncMock(side_effect=OutdoorViolation("busy"))
    with pytest.raises(OutdoorViolation):
        await s.close()
    s.hub.unregister_session.assert_called_once_with(s)
    assert s._closed
    s.outdoor.store.connections.pop(s.authenticated_device_id, None)
