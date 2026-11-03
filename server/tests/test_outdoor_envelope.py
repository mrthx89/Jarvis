"""Every v2 control response has a canonical request identity."""
import uuid
import pytest
from nexo_server.models.messages import VoiceprintDialog
from tests.test_outdoor_gateway import connect, control


@pytest.mark.asyncio
async def test_optional_legacy_request_none_does_not_escape_v2_output():
    s, ws = await connect()
    try:
        await control(s, "conversation_resume")
        await s.send_json_message(VoiceprintDialog(action="enroll"))
        value = ws.frames[-1]["request_id"]
        assert isinstance(value, str)
        assert str(uuid.UUID(value)) == value
    finally:
        await s.close()
