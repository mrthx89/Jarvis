import asyncio
import threading
from unittest.mock import AsyncMock

import pytest

from nexo_server.gateway.websocket_handler import ProtocolViolation
from tests.test_voiceprint_enrollment_protocol import make_session, pcm, control, dialogs, settle


@pytest.mark.asyncio
async def test_cancel_erases_capture_and_stale_controls_cannot_finish_next(tmp_path):
    session, verifier = make_session(tmp_path)
    try:
        await control(session, "voiceprint_enroll_start", "old")
        await session.handle_binary_frame(pcm())
        old = session._enrollment
        await control(session, "voiceprint_enroll_cancel", "old")
        assert dialogs(session)[-1]["action"] == "cancelled"
        assert not old["pcm"]
        await control(session, "voiceprint_enroll_start", "new")
        await control(session, "voiceprint_enroll_finish", "old")
        assert session._enrollment["request_id"] == "new"
        assert not verifier.is_enrolled()
        await control(session, "voiceprint_enroll_cancel", "new")
        await control(session, "voiceprint_enroll_start", "old")
        assert dialogs(session)[-1]["action"] == "error"
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_pcm_is_capped_without_automatic_commit(tmp_path):
    session, verifier = make_session(tmp_path)
    try:
        await control(session, "voiceprint_enroll_start")
        for _ in range(10):
            await session.handle_binary_frame(pcm())
        assert len(session._enrollment["pcm"]) == 192000
        assert not verifier.is_enrolled()
        await control(session, "voiceprint_enroll_finish")
        await settle(session)
        assert verifier.get_metadata()["duration_seconds"] == 6
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_timeout_and_disconnect_erase_capture(tmp_path):
    session, verifier = make_session(tmp_path)
    session.ENROLL_TIMEOUT = .02
    await control(session, "voiceprint_enroll_start")
    await session.handle_binary_frame(pcm())
    capture = session._enrollment
    await asyncio.sleep(.05)
    assert session._enrollment is None
    assert not capture["pcm"]
    assert dialogs(session)[-1]["success"] is False
    await control(session, "voiceprint_enroll_start", "disconnect")
    await session.handle_binary_frame(pcm())
    capture = session._enrollment
    await session.close()
    assert not capture["pcm"]
    assert not verifier.is_enrolled()


@pytest.mark.parametrize("termination", ["cancel", "timeout", "disconnect"])
@pytest.mark.asyncio
async def test_late_extraction_cannot_write_after_termination(tmp_path, termination):
    session, verifier = make_session(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = verifier.extract_embedding
    def blocking(audio):
        entered.set()
        release.wait(2)
        return original(audio)
    verifier.extract_embedding = blocking
    session.ENROLL_TIMEOUT = .05
    try:
        await control(session, "voiceprint_enroll_start")
        await session.handle_binary_frame(pcm())
        await control(session, "voiceprint_enroll_finish")
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(.001)
        assert entered.is_set()
        if termination == "cancel":
            await control(session, "voiceprint_enroll_cancel")
        elif termination == "disconnect":
            await session.close()
        else:
            await asyncio.sleep(.07)
        assert session._enrollment is None
        if termination != "disconnect":
            await control(session, "voiceprint_enroll_start", "while-worker-active")
            assert dialogs(session)[-1]["success"] is False
            assert session._enrollment is None
        release.set()
        await asyncio.sleep(.04)
        assert not verifier.is_enrolled()
        session.stt.transcribe.assert_not_called()
        session.tts.synthesize_stream.assert_not_called()
    finally:
        release.set()
        await session.close()


@pytest.mark.asyncio
async def test_start_busy_and_failed_delete_report_authoritative_profile(tmp_path):
    session, verifier = make_session(tmp_path)
    assert verifier.enroll(pcm())[0]
    verifier.unenroll = lambda name: (False, "denied")
    try:
        await control(session, "voiceprint_unenroll")
        result = dialogs(session)[-1]
        assert result["is_enrolled"] is True
        assert result["success"] is False
        session._external_speech = True
        await control(session, "voiceprint_enroll_start")
        assert dialogs(session)[-1]["success"] is False
        assert session._enrollment is None
    finally:
        await session.close()


@pytest.mark.parametrize("request_id", [None, "", " " , 1, True, "a" * 129])
@pytest.mark.asyncio
async def test_invalid_request_ids_do_not_capture(tmp_path, request_id):
    session, _ = make_session(tmp_path)
    try:
        with pytest.raises(ProtocolViolation):
            await control(session, "voiceprint_enroll_start", request_id)
        assert session._enrollment is None
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_auth_and_hello_still_required_for_enrollment(tmp_path):
    session, _ = make_session(tmp_path)
    session.authenticated_device_id = None
    with pytest.raises(ProtocolViolation):
        await control(session, "voiceprint_enroll_start")
    session.authenticated_device_id = "device"
    session.ready = False
    with pytest.raises(ProtocolViolation):
        await control(session, "voiceprint_enroll_start")
    await session.close()
