import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from nexo_server.gateway.websocket_handler import ApplianceSession
from nexo_server.pipeline.speaker import SpeakerVerifier


class FakeONNXSession:
    def get_inputs(self):
        return [SimpleNamespace(name="features")]

    def get_outputs(self):
        return [SimpleNamespace(name="embedding")]

    def run(self, outputs, inputs):
        features = next(iter(inputs.values()))
        # Deterministic explicit test model, not a production fallback.
        vector = np.resize(features.std(axis=1), 192).astype(np.float32)
        return [vector[None, :]]


def pcm(seconds: float = 2):
    t = np.arange(int(seconds * 16000)) / 16000
    return (12000 * np.sin(2 * np.pi * 440 * t)).astype(np.int16).tobytes()


def make_session(tmp_path, model=True):
    verifier = SpeakerVerifier(model_path=str(tmp_path / "missing.onnx"), voiceprints_dir=str(tmp_path / "profiles"))
    if model:
        verifier._session = FakeONNXSession()
    session = ApplianceSession(AsyncMock(), "test", AsyncMock(), MagicMock(), AsyncMock(),
                               authenticated_device_id="device", speaker_verifier=verifier)
    session.ready = True
    return session, verifier


async def control(session, kind, request_id="capture-1"):
    await session.handle_text_frame(json.dumps({"type": kind, "request_id": request_id}))


def dialogs(session):
    return [json.loads(c.args[0]) for c in session.websocket.send_text.call_args_list
            if json.loads(c.args[0])["type"] == "voiceprint_dialog"]


async def settle(session):
    for _ in range(200):
        if dialogs(session)[-1]["action"] != "recording":
            return
        await asyncio.sleep(.01)
    pytest.fail("enrollment did not complete")


@pytest.mark.asyncio
async def test_dedicated_pcm_never_reaches_conversation(tmp_path):
    session, verifier = make_session(tmp_path)
    try:
        await control(session, "voiceprint_enroll_start")
        assert dialogs(session)[-1]["action"] == "recording"
        assert dialogs(session)[-1]["available"] is True
        assert not verifier.is_enrolled()
        assert await session.speak_text("must not speak during capture") is False
        await session.handle_binary_frame(pcm())
        await control(session, "voice_end")
        await control(session, "voiceprint_enroll_finish")
        await settle(session)
        result = dialogs(session)[-1]
        assert result["action"] == "enrolled"
        assert result["request_id"] == "capture-1"
        assert result["success"] is True
        assert result["is_enrolled"] is True
        session.stt.transcribe.assert_not_called()
        session.hermes.query_stream.assert_not_called()
        session.tts.synthesize_stream.assert_not_called()
        assert not session.audio_buffer
    finally:
        await session.close()


@pytest.mark.parametrize("sample", [b"\x00" * 64000, pcm(.5)])
@pytest.mark.asyncio
async def test_failed_capture_preserves_existing_profile(tmp_path, sample):
    session, verifier = make_session(tmp_path)
    assert verifier.enroll(pcm())[0]
    before = {p.name: p.read_bytes() for p in verifier.voiceprints_dir.iterdir()}
    try:
        await control(session, "voiceprint_enroll_start")
        await session.handle_binary_frame(sample)
        await control(session, "voiceprint_enroll_finish")
        await settle(session)
        assert dialogs(session)[-1]["action"] == "error"
        assert dialogs(session)[-1]["success"] is False
        assert dialogs(session)[-1]["is_enrolled"] is True
        assert {p.name: p.read_bytes() for p in verifier.voiceprints_dir.iterdir()} == before
    finally:
        await session.close()


def test_missing_model_cannot_produce_biometric_embedding(tmp_path):
    _, verifier = make_session(tmp_path, model=False)
    assert verifier.extract_embedding(pcm()) is None
    assert verifier.enroll(pcm())[0] is False
    assert not verifier.is_enrolled()


@pytest.mark.asyncio
async def test_missing_model_refuses_capture(tmp_path):
    session, verifier = make_session(tmp_path, model=False)
    try:
        await control(session, "voiceprint_enroll_start")
        result = dialogs(session)[-1]
        assert result["action"] == "error"
        assert result["available"] is False
        assert result["success"] is False
        assert not verifier.is_enrolled()
    finally:
        await session.close()


def test_permission_error_never_opens_verification(tmp_path, monkeypatch):
    _, verifier = make_session(tmp_path)
    def inaccessible(path, *args, **kwargs):
        raise PermissionError("blocked")
    monkeypatch.setattr(type(verifier.voiceprints_dir), "stat", inaccessible)
    assert verifier.verify(pcm()) == (False, 0.0)
    with pytest.raises(PermissionError):
        verifier.is_enrolled()


def test_failed_replacement_preserves_all_profile_bytes(tmp_path, monkeypatch):
    import nexo_server.pipeline.speaker as speaker
    _, verifier = make_session(tmp_path)
    assert verifier.enroll(pcm())[0]
    before = {p.name: p.read_bytes() for p in verifier.voiceprints_dir.iterdir()}
    def fail_write(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(speaker.os, "chmod", fail_write)
    assert verifier.enroll(pcm(3))[0] is False
    assert {p.name: p.read_bytes() for p in verifier.voiceprints_dir.iterdir()} == before


def test_failed_atomic_promotion_restores_metadata_and_keeps_embedding(tmp_path, monkeypatch):
    import nexo_server.pipeline.speaker as speaker
    _, verifier = make_session(tmp_path)
    assert verifier.enroll(pcm())[0]
    before = {p.name: p.read_bytes() for p in verifier.voiceprints_dir.iterdir()}
    original_replace = speaker.os.replace
    def fail_biometric_promotion(source, target):
        if str(target).endswith("owner.npy"):
            raise OSError("promotion failed")
        return original_replace(source, target)
    monkeypatch.setattr(speaker.os, "replace", fail_biometric_promotion)
    assert verifier.enroll(pcm(3))[0] is False
    assert {p.name: p.read_bytes() for p in verifier.voiceprints_dir.iterdir()} == before


@pytest.mark.parametrize("utterance", ["daftarkan suara saya", "hapus sidik suara"])
@pytest.mark.asyncio
async def test_voice_commands_only_open_confirmation_ui(tmp_path, utterance):
    session, verifier = make_session(tmp_path)
    assert verifier.enroll(pcm())[0]
    before = {p.name: p.read_bytes() for p in verifier.voiceprints_dir.iterdir()}
    session.stt.transcribe.return_value = utterance
    session._speak_canned_reply = AsyncMock()
    try:
        await session._run_pipeline(pcm(3))
        assert {p.name: p.read_bytes() for p in verifier.voiceprints_dir.iterdir()} == before
        assert dialogs(session)[-1]["action"] in ("enroll", "manage")
        assert dialogs(session)[-1]["success"] is None
        session.hermes.query_stream.assert_not_called()
    finally:
        await session.close()
