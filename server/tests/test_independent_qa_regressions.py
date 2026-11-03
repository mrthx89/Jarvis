"""Independent contract probes. Desired outcomes intentionally fail on reviewed HEAD."""
import asyncio
import time
from types import SimpleNamespace
import pytest
from tests.test_outdoor_runtime import Session, command, uid, metadata, frame, enrollment_session, sample
from tests.test_outdoor_gateway import connect, control
from nexo_server.session_hub import SessionHub
from nexo_server.answer_mirror import AnswerMirror

@pytest.mark.asyncio
async def test_cancel_before_begin_is_terminal():
    s = Session()
    await s.outdoor.hello()
    await command(s, 'conversation_resume')
    u = uid()
    try:
        assert (await command(s, 'utterance_cancel', utterance_id=u, reason='cancelled'))['stage'] == 'cancelled'
        actual = await command(s, 'utterance_status', utterance_id=u)
        assert actual['stage'] == 'cancelled', actual
    finally:
        await s.outdoor.close()

@pytest.mark.asyncio
async def test_cancel_after_saved_reports_saved_not_cancelled():
    s, identity = await enrollment_session()
    try:
        ids = [await sample(s, identity) for _ in range(4)]
        result = await command(s, 'enrollment_save', **identity, sample_utterance_ids=ids[:3], holdout_utterance_id=ids[3])
        assert result['state'] == 'saved', result
        assert s.speaker_verifier.saved
        actual = await command(s, 'enrollment_cancel', **identity)
        assert actual['state'] in {'saved', 'already_saved'}, actual
    finally:
        await s.outdoor.close()

@pytest.mark.asyncio
async def test_inactivity_five_seconds_aborts_without_dispatch():
    s = Session()
    await s.outdoor.hello()
    await command(s, 'conversation_resume')
    pcm = b'\x01\x00' * 4
    meta = metadata(pcm)
    try:
        await command(s, 'utterance_begin', **meta)
        await s.outdoor.binary(frame(meta['utterance_id'], 0, pcm[:2]))
        await asyncio.sleep(5.2)
        assert s.outdoor.assembly is None, 'assembly survived >5s inactivity'
        assert not s.runs
        assert any(m.get('code') == 'upload_timeout' for m in s.sent)
    finally:
        await s.outdoor.close()

@pytest.mark.asyncio
async def test_terminal_queries_reserve_one_dispatch():
    s, ws = await connect()
    hub = SessionHub()
    hub.register_session(s)
    started, finish = asyncio.Event(), asyncio.Event()
    called = []
    async def query(message):
        called.append(message)
        started.set()
        await finish.wait()
        return 'fixture'
    hub._hermes_bridge = SimpleNamespace(query=query)
    first = second = None
    try:
        await control(s, 'conversation_resume')
        first = asyncio.create_task(hub.inject_message('A'))
        await asyncio.wait_for(started.wait(), 1)
        second = asyncio.create_task(hub.inject_message('B'))
        for _ in range(15):
            await asyncio.sleep(0)
        assert called == ['A'], f'concurrent Hermes dispatches={called}'
        assert second.done() and second.result()['status'] == 'busy'
    finally:
        finish.set()
        await asyncio.gather(*(t for t in (first, second) if t), return_exceptions=True)
        await s.close()

@pytest.mark.asyncio
async def test_mirror_pending_discarded_at_sheet_acquire():
    s, ws = await connect()
    hub = SessionHub()
    hub.register_session(s)
    reader = SimpleNamespace(high_water=lambda: 4)
    mirror = AnswerMirror(reader, hub, SimpleNamespace(query_active=False))
    mirror.pending.append(dict(id=4, text='pre-sheet final', queued_at=time.monotonic()))
    try:
        await control(s, 'conversation_resume')
        owner = uid()
        await control(s, 'audio_owner_acquire', owner_id=owner, purpose='diagnostic', policy='cancel_active')
        assert s.outdoor.paused
        assert not mirror.pending, 'queued pre-sheet final retained across acquisition'
    finally:
        await s.close()
