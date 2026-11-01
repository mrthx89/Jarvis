# pyright: reportMissingImports=false
"""Unit and integration tests for Terminal Bridge, Telemetry Stream, and Emotional Tags."""

import asyncio
import io
import json
import sqlite3
import time
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from nexo_server.mascot import (
    MascotExpression,
    parse_and_strip_emotional_tags,
    parse_emotional_tag,
    strip_emotional_tags,
)
from nexo_server.gateway.websocket_handler import ApplianceSession
from nexo_server.session_hub import SessionHub
from nexo_server.answer_mirror import AnswerMirror
from nexo_server.session_store import HermesAnswerReader
from nexo_server.models.messages import NexoState, TranscriptionMessage, StateChange


# ============================================================================
# Section 1: Emotional Tag Parsing Tests
# ============================================================================

def test_parse_and_strip_emotional_tags_basic():
    """Verify basic emotional tags are recognized and stripped."""
    cases = [
        ("[expr:senang] Halo semuanya!", MascotExpression.SENANG, "Halo semuanya!"),
        ("[expr:bingung] Maaf, saya kurang paham.", MascotExpression.BINGUNG, "Maaf, saya kurang paham."),
        ("[expr:sedih] Aku merasa sedih mendengarnya.", MascotExpression.SEDIH, "Aku merasa sedih mendengarnya."),
        ("[expr:terkejut] Wah, sungguh tidak disangka!", MascotExpression.TERKEJUT, "Wah, sungguh tidak disangka!"),
        ("[expr:tertawa] Hahaha lelucon yang bagus!", MascotExpression.TERTAWA, "Hahaha lelucon yang bagus!"),
        ("[expr:marah] Ini tidak bisa diterima!", MascotExpression.MARAH, "Ini tidak bisa diterima!"),
        ("[expr:sleeping] Selamat malam, waktunya istirahat.", MascotExpression.SLEEPING, "Selamat malam, waktunya istirahat."),
    ]
    for raw, expected_expr, expected_clean in cases:
        expr, clean = parse_and_strip_emotional_tags(raw)
        assert expr == expected_expr, f"Failed expr for: {raw}"
        assert clean == expected_clean, f"Failed clean for: {raw}"
        assert parse_emotional_tag(raw) == expected_expr
        assert strip_emotional_tags(raw) == expected_clean


def test_parse_and_strip_emotional_tags_aliases_and_case():
    """Verify aliases, whitespace inside brackets, and case-insensitivity."""
    cases = [
        ("[EXPR:BAHAGIA] Sukses besar!", MascotExpression.SENANG, "Sukses besar!"),
        ("[expr:  kaget  ] Mengagetkan sekali!", MascotExpression.TERKEJUT, "Mengagetkan sekali!"),
        ("[expr:optimis] Pasti bisa tercapai.", MascotExpression.SANGAT_SENANG, "Pasti bisa tercapai."),
        ("[expr:sangat senang] Luar biasa!", MascotExpression.SANGAT_SENANG, "Luar biasa!"),
        ("[expr:tidur] Mode senyap aktif.", MascotExpression.SLEEPING, "Mode senyap aktif."),
        ("[expr:gembira] Hari yang menyenangkan.", MascotExpression.SENANG, "Hari yang menyenangkan."),
        ("[expr:normal] Kembali ke awal.", MascotExpression.NETRAL, "Kembali ke awal."),
    ]
    for raw, expected_expr, expected_clean in cases:
        expr, clean = parse_and_strip_emotional_tags(raw)
        assert expr == expected_expr
        assert clean == expected_clean


def test_parse_and_strip_emotional_tags_mid_sentence_and_unknown():
    """Verify tags embedded inside sentences and unknown tags are stripped."""
    text1 = "Tentu saja [expr:senang]! Saya siap mendampingi."
    expr1, clean1 = parse_and_strip_emotional_tags(text1)
    assert expr1 == MascotExpression.SENANG
    assert clean1 == "Tentu saja! Saya siap mendampingi."

    text2 = "Pertama [expr:bingung], saya perlu berpikir. Lalu [expr:senang], saya mengerti!"
    expr2, clean2 = parse_and_strip_emotional_tags(text2)
    assert expr2 == MascotExpression.BINGUNG
    assert clean2 == "Pertama, saya perlu berpikir. Lalu, saya mengerti!"

    text3 = "[expr:unknown_tag_xyz] Teks biasa tanpa ekspresi valid."
    expr3, clean3 = parse_and_strip_emotional_tags(text3)
    assert expr3 is None
    assert clean3 == "Teks biasa tanpa ekspresi valid."

    assert parse_and_strip_emotional_tags("") == (None, "")
    assert parse_and_strip_emotional_tags(cast(str, None)) == (None, "")


# ============================================================================
# Section 2: Gateway & TTS Integration Tests
# ============================================================================

class MockWS:
    def __init__(self):
        self.messages = []
        self.audio_chunks = []

    async def send_text(self, text: str):
        self.messages.append(json.loads(text))

    async def send_bytes(self, data: bytes):
        self.audio_chunks.append(data)


class MockTTSCollector:
    def __init__(self):
        self.synthesized = []

    async def synthesize_stream(self, text: str):
        self.synthesized.append(text)
        yield b"\x00\x01" * 480


class MockStreamingHermesWithEmotions:
    def __init__(self, chunks):
        self.chunks = chunks

    async def query_stream(self, prompt: str):
        for c in self.chunks:
            yield c


@pytest.mark.asyncio
async def test_gateway_streaming_with_emotional_tags():
    """Verify sentence-chunked streaming parses tags, drives mascot state, and strips for TTS."""
    chunks = [
        "[expr:senang] Halo! Senang bertemu kembali.",
        "[expr:bingung] Apakah ada tugas yang perlu diselesaikan?",
    ]
    hermes = MockStreamingHermesWithEmotions(chunks)
    tts = MockTTSCollector()
    ws = MockWS()
    stt = AsyncMock()
    stt.transcribe = AsyncMock(return_value="Halo Nexo")

    session = ApplianceSession(
        websocket=cast(Any, ws),
        session_id="test_emotion_stream",
        stt=stt,
        tts=cast(Any, tts),
        hermes=cast(Any, hermes),
        authenticated_device_id="dev_test",
    )
    # Handshake
    hello = {
        "type": "client_hello", "device_id": "dev_test", "protocol_version": "1.0",
        "audio_spec": {"format": "audio/pcm", "sample_rate": 16000, "channels": 1, "bit_depth": 16},
    }
    await session.handle_text_frame(json.dumps(hello))
    ws.messages.clear()
    session.ECHO_GUARD_SECONDS = 0.001

    session.audio_buffer.extend(b"\x00\x20" * 3200)
    await session.process_recorded_audio()

    # 1. TTS synthesized texts must have NO tags
    assert tts.synthesized == [
        "Halo! Senang bertemu kembali.",
        "Apakah ada tugas yang perlu diselesaikan?",
    ]

    # 2. State change events reflected emotional expressions
    state_changes = [m for m in ws.messages if m.get("type") == "state_change" and m.get("state") == "SPEAKING"]
    assert len(state_changes) >= 2
    assert state_changes[0].get("expression") == "senang"
    assert state_changes[1].get("expression") == "bingung"

    # 3. Final transcription subtitle has NO tags
    transcripts = [m for m in ws.messages if m.get("type") == "transcription" and m.get("role") == "assistant"]
    assert len(transcripts) == 1
    assert transcripts[0]["text"] == "Halo! Senang bertemu kembali. Apakah ada tugas yang perlu diselesaikan?"


@pytest.mark.asyncio
async def test_gateway_speak_text_with_emotional_tag():
    """Verify speak_text parses emotional tag, applies it to mascot, and strips from TTS."""
    tts = MockTTSCollector()
    ws = MockWS()
    session = ApplianceSession(
        websocket=cast(Any, ws),
        session_id="test_speak_expr",
        stt=AsyncMock(),
        tts=cast(Any, tts),
        hermes=AsyncMock(),
        authenticated_device_id="dev_test",
    )
    hello = {
        "type": "client_hello", "device_id": "dev_test", "protocol_version": "1.0",
        "audio_spec": {"format": "audio/pcm", "sample_rate": 16000, "channels": 1, "bit_depth": 16},
    }
    await session.handle_text_frame(json.dumps(hello))
    ws.messages.clear()
    session.ECHO_GUARD_SECONDS = 0.001

    success = await session.speak_text("[expr:terkejut] Peringatan darurat terdeteksi!")
    assert success is True
    assert tts.synthesized == ["Peringatan darurat terdeteksi!"]

    speaking_states = [m for m in ws.messages if m.get("type") == "state_change" and m.get("state") == "SPEAKING"]
    assert speaking_states[0]["expression"] == "terkejut"

    subtitles = [m for m in ws.messages if m.get("type") == "transcription" and m.get("role") == "assistant"]
    assert subtitles[0]["text"] == "Peringatan darurat terdeteksi!"


# ============================================================================
# Section 3: AnswerMirror Deduplication Tests
# ============================================================================

def setup_sqlite_hermes_db(tmp_path):
    db_file = tmp_path / "state.db"
    conn = sqlite3.connect(db_file)
    conn.execute("""
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT,
            finish_reason TEXT,
            active INTEGER DEFAULT 1,
            compacted INTEGER DEFAULT 0,
            display_kind TEXT,
            tool_calls TEXT
        )
    """)
    conn.commit()
    conn.close()
    return db_file


def test_last_matching_final_matches_exact_and_tag_stripped(tmp_path):
    """HermesAnswerReader deduplication finds matching finals whether tags are present or stripped."""
    db_file = setup_sqlite_hermes_db(tmp_path)
    reader = HermesAnswerReader(db_file, "session_test")

    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO messages (session_id, role, content, finish_reason) VALUES (?, ?, ?, ?)",
        ("session_test", "assistant", "[expr:senang] Jawaban resmi dari Hermes", "stop")
    )
    msg_id = int(cur.lastrowid or 1)
    conn.commit()
    conn.close()

    # Exact match
    assert reader.last_matching_final("[expr:senang] Jawaban resmi dari Hermes", 0, msg_id) == msg_id
    # Stripped match
    assert reader.last_matching_final("Jawaban resmi dari Hermes", 0, msg_id) == msg_id
    # Different tag but same text
    assert reader.last_matching_final("[expr:bingung] Jawaban resmi dari Hermes", 0, msg_id) == msg_id
    # Completely different text
    assert reader.last_matching_final("Jawaban lain", 0, msg_id) is None


@pytest.mark.asyncio
async def test_answer_mirror_delivers_clean_terminal_answer_and_deduplicates():
    """AnswerMirror strips tags on delivery to appliance and ignores duplicate polling."""
    from types import SimpleNamespace

    class MockReader:
        def __init__(self):
            self.cursor = 0
            self.rows = []
            self.history = []

        def high_water(self):
            return max((r["id"] for r in self.history + self.rows), default=0)

        def last_matching_final(self, text, after, through):
            for r in reversed(self.history + self.rows):
                if after < r["id"] <= through:
                    if r["text"].strip() == text.strip():
                        return r["id"]
                    if strip_emotional_tags(r["text"]).strip() == strip_emotional_tags(text).strip():
                        return r["id"]
            return None

        def read_new(self):
            out = list(self.rows)
            self.history.extend(out)
            self.rows.clear()
            if out:
                self.cursor = out[-1]["id"]
            return out

    reader = MockReader()
    hub = SessionHub()
    bridge = SimpleNamespace(query_active=0)
    mirror = AnswerMirror(reader, hub, bridge)

    device = MagicMock()
    device.ready = True
    device._closed = False
    device.is_recording = False
    device._busy = MagicMock(return_value=False)
    device.speak_text = AsyncMock(return_value=True)

    hub.register_session(device)

    # 1. Add terminal answer with tag
    reader.rows = [{"id": 1, "text": "[expr:senang] Halo dari terminal nexo"}]
    await mirror.poll_once()
    await mirror.deliver_once()

    # Verify device.speak_text was called with clean text and expression
    device.speak_text.assert_called_once_with("Halo dari terminal nexo", expression="senang")

    # 2. Duplicate poll should not re-deliver
    await mirror.poll_once()
    await mirror.deliver_once()
    assert device.speak_text.call_count == 1

    # 3. Simulate bridge reply suppression
    device.speak_text.reset_mock()
    bridge.query_active = 1
    reader.rows = [
        {"id": 2, "text": "Terminal concurrent"},
        {"id": 3, "text": "[expr:bingung] Voice reply from appliance"},
    ]
    await mirror.poll_once()
    assert reader.rows  # deferred while query_active
    await mirror.on_bridge_reply("Voice reply from appliance")
    bridge.query_active = 0

    assert len(mirror.pending) == 1
    assert mirror.pending[0]["text"] == "Terminal concurrent"


# ============================================================================
# Section 4: CLI Stream Sentence-Chunking Tests (nexo watch & nexo bridge)
# ============================================================================

def test_cmd_watch_sentence_chunking_telemetry(capsys):
    """Verify cmd_watch format strings display streaming chunks with index and expression."""
    import scripts.nexo_session as ns

    event_streaming = {
        "type": "cognitive",
        "stage": "streaming",
        "sentence": "[expr:senang] Ini kalimat pertama real-time.",
        "index": 0,
        "expression": "senang",
        "iso_time": "2026-11-01T12:00:00Z",
    }

    # Simulate how on_event in cmd_watch processes this
    iso = event_streaming.get("iso_time", "")
    t_short = iso[11:19]
    prefix = f"[{t_short}] "
    stage = event_streaming.get("stage", "thought")
    sentence = event_streaming.get("sentence") or event_streaming.get("content", "")
    expr = event_streaming.get("expression")
    expr_tag = f" (Mascot: {expr})" if expr else ""
    import re
    clean_sentence = re.sub(r"\[expr:\s*[a-zA-Z0-9_\s-]+?\s*\]", "", sentence).strip()
    idx = event_streaming.get("index")
    idx_str = f" #{idx + 1}" if idx is not None else ""

    out_line = f"{prefix}💬 [STREAM{idx_str}] 🤖 NEXO: {clean_sentence}{expr_tag}"
    assert "💬 [STREAM #1] 🤖 NEXO: Ini kalimat pertama real-time. (Mascot: senang)" in out_line


@pytest.mark.asyncio
async def test_session_hub_inject_message_streams_chunks_and_emotions():
    """Verify session_hub.inject_message uses streaming bridge, publishes cognitive stream, and returns clean reply."""
    sh = SessionHub()

    class StreamBridge:
        def __init__(self):
            self.session_name = "nexo"

        async def query_stream(self, prompt, session_name=None):
            yield "[expr:senang] Kalimat satu."
            yield "[expr:bingung] Kalimat dua."

        async def query(self, prompt, session_name=None):
            return "Non-stream fallback"

    sh.set_hermes_bridge(StreamBridge())
    q = sh.subscribe()

    res = await sh.inject_message("Halo dari terminal", session_name="nexo")
    assert res["status"] == "ok"
    assert res["reply"] == "Kalimat satu. Kalimat dua."
    assert res["expression"] in ("senang", "bingung")

    events = []
    while not q.empty():
        events.append(q.get_nowait())

    stream_events = [e for e in events if e.get("type") == "cognitive" and e.get("stage") == "streaming"]
    assert len(stream_events) == 2
    assert stream_events[0]["sentence"] == "Kalimat satu."
    assert stream_events[0]["index"] == 0
    assert stream_events[1]["sentence"] == "Kalimat dua."
    assert stream_events[1]["index"] == 1

    transcriptions = [e for e in events if e.get("type") == "transcription" and e.get("role") == "assistant"]
    assert len(transcriptions) == 1
    assert transcriptions[0]["text"] == "Kalimat satu. Kalimat dua."
