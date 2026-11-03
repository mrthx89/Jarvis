"""Offline wire/runtime policy tests; no model, network or production profile."""
import asyncio
import hashlib
import struct
import uuid
import threading
from typing import Any

import numpy as np
import pytest

from nexo_server.pipeline.speaker import FRONTEND_ID, MODEL_SHA256
from nexo_server.gateway.outdoor import OutdoorSession, OutdoorStore, OutdoorViolation


def metadata(pcm, **fields):
    return dict(utterance_id=uid(), purpose="conversation", total_samples=len(pcm)//2,
                sha256=hashlib.sha256(pcm).hexdigest(), end_reason="manual_finish", **fields)


def frame(utterance, offset, pcm):
    return struct.pack(">4s16sII", b"NXA2", uuid.UUID(utterance).bytes, offset, len(pcm)) + pcm


async def upload(s, pcm, meta=None):
    meta = meta or metadata(pcm)
    await command(s, "utterance_begin", **meta)
    for start in range(0, len(pcm), 16384):
        await s.outdoor.binary(frame(meta["utterance_id"], start//2, pcm[start:start+16384]))
    result = await command(s, "utterance_commit", **{key: meta[key] for key in
                            ("utterance_id", "total_samples", "sha256")})
    return meta, result


async def settle(s):
    if s.outdoor.active_task:
        await s.outdoor.active_task


@pytest.mark.asyncio
async def test_whole_pcm_integrity_ack_precedes_single_conversation_dispatch():
    s = Session()
    await s.outdoor.hello()
    await command(s, "conversation_resume")
    pcm = b"\x01\x00" * 18000 + b"\x00\x00" * 18000 + b"\xff\x00" * 18000
    async def run(captured):
        assert s.sent[-1]["stage"] == "accepted"
        assert captured == pcm
        s.runs.append(captured)
        return "ok"
    s._run_pipeline = run
    meta, accepted = await upload(s, pcm)
    assert accepted["stage"] == "accepted"
    assert accepted["total_samples"] == len(pcm)//2
    assert accepted["sha256"] == meta["sha256"]
    await settle(s)
    assert s.runs == [pcm]
    assert s.sent[-1]["type"] == "utterance_result"
    assert s.sent[-1]["utterance_id"] == meta["utterance_id"]
    assert s.sent[-1]["status"] == "completed"
    await s.outdoor.close()

@pytest.mark.parametrize("field,value", [("total_samples", True), ("total_samples", 1.0),
    ("total_samples", 0), ("total_samples", 320001), ("sha256", "F"*64),
    ("sha256", "a"*63), ("purpose", "diagnostic"), ("end_reason", "invalid")])
@pytest.mark.asyncio
async def test_begin_metadata_strictly_validated(field, value):
    s = Session()
    await s.outdoor.hello()
    await command(s, "conversation_resume")
    meta = metadata(b"\x00\x00")
    meta[field] = value
    result = await command(s, "utterance_begin", **meta)
    assert result["type"] == "utterance_error"
    assert not s.runs
    assert s.outdoor.assembly is None
    await s.outdoor.close()


@pytest.mark.parametrize("case,code", [("gap", "sequence_gap"), ("overlap", "overlap_conflict"),
    ("oversize", "invalid_length"), ("truncated", "invalid_format"),
    ("odd", "invalid_length"), ("wrong_id", "invalid_format"),
    ("raw", "invalid_format"), ("overrun", "invalid_length")])
@pytest.mark.asyncio
async def test_binary_violation_never_dispatches_partial_audio(case, code):
    s = Session()
    await s.outdoor.hello()
    await command(s, "conversation_resume")
    meta = metadata(b"\x01\x00" * 4)
    await command(s, "utterance_begin", **meta)
    await s.outdoor.binary(frame(meta["utterance_id"], 0, b"\x01\x00"))
    bad = {
        "gap": frame(meta["utterance_id"], 2, b"\x01\x00"),
        "overlap": frame(meta["utterance_id"], 0, b"\x02\x00"),
        "oversize": frame(meta["utterance_id"], 1, b"\x00" * 16386),
        "truncated": frame(meta["utterance_id"], 1, b"\x00\x00")[:-1],
        "odd": frame(meta["utterance_id"], 1, b"\x00"),
        "wrong_id": frame(uid(), 1, b"\x00\x00"),
        "raw": b"\x00"*2,
        "overrun": frame(meta["utterance_id"], 1, b"\x00"*8),
    }[case]
    with pytest.raises(OutdoorViolation, match=code):
        await s.outdoor.binary(bad)
    assert not s.runs
    await s.outdoor.close()


@pytest.mark.asyncio
async def test_exact_duplicate_range_does_not_append():
    s = Session()
    await s.outdoor.hello()
    await command(s, "conversation_resume")
    pcm = b"\x01\x00\x02\x00"
    meta = metadata(pcm)
    await command(s, "utterance_begin", **meta)
    await s.outdoor.binary(frame(meta["utterance_id"], 0, pcm))
    await s.outdoor.binary(frame(meta["utterance_id"], 0, pcm[:2]))
    assert s.sent[-1]["next_sample"] == 2
    await command(s, "utterance_commit", **{k: meta[k] for k in ("utterance_id", "total_samples", "sha256")})
    await settle(s)
    assert s.runs == [pcm]
    await s.outdoor.close()


@pytest.mark.parametrize("changed", ["total_samples", "sha256"])
@pytest.mark.asyncio
async def test_commit_is_bound_to_original_metadata(changed):
    s = Session()
    await s.outdoor.hello()
    await command(s, "conversation_resume")
    meta = metadata(b"\x01\x00")
    await command(s, "utterance_begin", **meta)
    await s.outdoor.binary(frame(meta["utterance_id"], 0, b"\x01\x00"))
    commit = {k: meta[k] for k in ("utterance_id", "total_samples", "sha256")}
    commit[changed] = 2 if changed == "total_samples" else "0"*64
    result = await command(s, "utterance_commit", **commit)
    assert result["code"] == "id_conflict"
    assert not s.runs
    await s.outdoor.close()


@pytest.mark.asyncio
async def test_paused_conversation_upload_refused():
    s = Session()
    await s.outdoor.hello()
    result = await command(s, "utterance_begin", **metadata(b"\x01\x00"))
    assert result["code"] == "owner_required"
    await s.outdoor.close()


@pytest.mark.asyncio
async def test_accepted_dedup_survives_disconnect_and_reconnect():
    store = OutdoorStore()
    s = Session(store)
    await s.outdoor.hello()
    await command(s, "conversation_resume")
    meta, _ = await upload(s, b"\x01\x00")
    await settle(s)
    await s.outdoor.close()
    other = Session(store)
    await other.outdoor.hello()
    status = await command(other, "utterance_status", utterance_id=meta["utterance_id"])
    assert status["stage"] == "completed"
    for kind in ("utterance_begin", "utterance_commit"):
        fields = meta if kind.endswith("begin") else {k: meta[k] for k in
                       ("utterance_id", "total_samples", "sha256")}
        result = await command(other, kind, **fields)
        assert result["stage"] == "completed"
        assert result["duplicate"] is True
        assert result["sha256"] == meta["sha256"]
        assert result["total_samples"] == meta["total_samples"]
    assert not other.runs
    await other.outdoor.close()


@pytest.mark.asyncio
async def test_pending_duplicate_commit_and_begin_reserve_one_dispatch():
    s = Session()
    await s.outdoor.hello()
    await command(s, "conversation_resume")
    gate = asyncio.Event()
    started = asyncio.Event()
    async def run(pcm):
        s.runs.append(pcm)
        started.set()
        await gate.wait()
    s._run_pipeline = run
    meta, _ = await upload(s, b"\x01\x00")
    await started.wait()
    for kind in ("utterance_begin", "utterance_commit"):
        result = await command(s, kind, **meta)
        assert result["stage"] == "accepted"
        assert result["duplicate"] is True
    assert len(s.runs) == 1
    gate.set()
    await settle(s)
    await s.outdoor.close()


@pytest.mark.asyncio
async def test_live_processing_is_cancelled_on_disconnect_and_never_replayed():
    s = Session()
    await s.outdoor.hello()
    await command(s, "conversation_resume")
    started = asyncio.Event()
    async def run(pcm):
        started.set()
        await asyncio.Event().wait()
    s._run_pipeline = run
    meta, _ = await upload(s, b"\x01\x00")
    await started.wait()
    await s.outdoor.close()
    other = Session(s.outdoor_store)
    await other.outdoor.hello()
    result = await command(other, "utterance_status", utterance_id=meta["utterance_id"])
    assert result["stage"] == "cancelled"
    assert s.outdoor.active_task.done()
    assert not any(m.get("status") == "completed" for m in s.sent)
    await other.outdoor.close()


@pytest.mark.parametrize("limit", ["device", "global"])
@pytest.mark.asyncio
async def test_ledger_never_evicts_young_or_pending_entries(limit):
    s = Session()
    await s.outdoor.hello()
    await command(s, "conversation_resume")
    for i in range(128 if limit == "device" else 512):
        s.outdoor_store.ledger[(s.authenticated_device_id if limit == "device" else str(i), uid())] = {
            "stage": "completed", "terminal_at": s.outdoor_store.clock()}
    result = await command(s, "utterance_begin", **metadata(b"\x01\x00"))
    assert result["code"] == "capacity"
    await s.outdoor.close()


@pytest.mark.asyncio
async def test_old_terminal_entries_can_be_pruned_but_pending_cannot():
    s = Session()
    now = [500.0]
    s.outdoor_store.clock = lambda: now[0]
    for _ in range(128):
        s.outdoor_store.ledger[(s.authenticated_device_id, uid())] = {
            "stage": "completed", "terminal_at": 0.0}
    await s.outdoor.hello()
    await command(s, "conversation_resume")
    result = await command(s, "utterance_begin", **metadata(b"\x01\x00"))
    assert result["stage"] == "begin"
    assert len(s.outdoor_store.ledger) == 1
    await s.outdoor.close()


@pytest.mark.asyncio
async def test_incomplete_upload_wall_timeout_never_invokes_pipeline():
    s = Session()
    s.outdoor.UPLOAD_TIMEOUT = 0.01
    await s.outdoor.hello()
    await command(s, "conversation_resume")
    epoch = s.outdoor.epoch
    await command(s, "utterance_begin", **metadata(b"\x01\x00"))
    await asyncio.sleep(0.04)
    assert s.sent[-1]["code"] == "upload_timeout"
    assert s.sent[-1]["output_epoch"] == epoch + 1
    assert s.outdoor.paused
    assert s.outdoor.assembly is None
    assert not s.runs
    rejected = await command(s, "utterance_begin", **metadata(b"\x01\x00"))
    assert rejected["code"] == "owner_required"
    await command(s, "conversation_resume")
    await upload(s, b"\x01\x00")
    await settle(s)
    assert s.runs == [b"\x01\x00"]
    await s.outdoor.close()


@pytest.mark.asyncio
async def test_processing_timeout_emits_terminal_and_fences_late_work():
    s = Session()
    s.outdoor.PROCESSING_TIMEOUT = 0.01
    await s.outdoor.hello()
    await command(s, "conversation_resume")
    async def run(pcm):
        await asyncio.Event().wait()
    s._run_pipeline = run
    await upload(s, b"\x01\x00")
    await asyncio.sleep(0.05)
    assert s.sent[-1]["code"] == "processing_timeout"
    assert s.sent[-1]["status"] == "failed"
    await s.outdoor.close()


@pytest.mark.asyncio
async def test_cancel_is_idempotent_and_fences_processing():
    s = Session()
    await s.outdoor.hello()
    await command(s, "conversation_resume")
    async def run(pcm):
        await asyncio.Event().wait()
    s._run_pipeline = run
    meta, _ = await upload(s, b"\x01\x00")
    for _ in range(2):
        result = await command(s, "utterance_cancel", utterance_id=meta["utterance_id"], reason="cancelled")
        assert result["stage"] == "cancelled"
    assert not any(m.get("status") == "completed" for m in s.sent)
    await s.outdoor.close()


@pytest.mark.asyncio
async def test_changed_begin_metadata_cannot_replace_pending_identity():
    s = Session()
    await s.outdoor.hello()
    await command(s, "conversation_resume")
    meta = metadata(b"\x01\x00")
    await command(s, "utterance_begin", **meta)
    changed = dict(meta, end_reason="vad_end")
    result = await command(s, "utterance_begin", **changed)
    assert result["code"] == "id_conflict"
    await s.outdoor.close()


@pytest.mark.asyncio
async def test_acquire_replay_preserves_live_lease_without_replacing_owner():
    s = Session()
    await s.outdoor.hello()
    payload = dict(type="audio_owner_acquire", connection_id=s.outdoor.connection_id,
                   request_id=uid(), owner_id=uid(), purpose="enrollment", policy="cancel_active")
    await s.outdoor.control(payload)
    granted = dict(s.sent[-1])
    deadline = s.outdoor.owner["deadline"]
    generation = s.outdoor.generation
    await s.outdoor.control(payload)
    assert s.sent[-1] == granted
    payload["request_id"] = uid()
    await s.outdoor.control(payload)
    assert s.sent[-1]["lease_id"] == granted["lease_id"]
    assert s.outdoor.owner["deadline"] == deadline
    assert s.outdoor.generation == generation
    with pytest.raises(OutdoorViolation, match="busy"):
        await acquire(s, "diagnostic")
    assert s.outdoor.owner["lease_id"] == granted["lease_id"]
    await s.outdoor.close()


@pytest.mark.parametrize("release_first", [False, True])
@pytest.mark.asyncio
async def test_released_owner_cannot_resurrect_until_tombstone_expires(release_first):
    store = OutdoorStore()
    now = [500.0]
    store.clock = lambda: now[0]
    s = Session(store)
    await s.outdoor.hello()
    owner_id, request_id = uid(), uid()
    fields = dict(owner_id=owner_id, purpose="enrollment", policy="cancel_active")
    payload = dict(type="audio_owner_acquire", connection_id=s.outdoor.connection_id,
                   request_id=request_id, **fields)
    if not release_first:
        await s.outdoor.control(payload)
    await command(s, "audio_owner_release", owner_id=owner_id)
    await s.outdoor.close()
    s = Session(store)
    await s.outdoor.hello()
    payload["connection_id"] = s.outdoor.connection_id
    for elapsed in (0, 119.999):
        now[0] = 500 + elapsed
        await s.outdoor.control(payload)
        assert s.sent[-1]["state"] == "denied"
        assert s.sent[-1]["code"] == "owner_required"
        assert s.outdoor.owner is None
    now[0] = 620
    await s.outdoor.control(payload)
    assert s.sent[-1]["state"] == "granted"
    await s.outdoor.close()


@pytest.mark.asyncio
async def test_owner_cache_capacity_fails_closed_without_evicting_young_tombstones():
    s = Session()
    now = [500.0]
    s.outdoor_store.clock = lambda: now[0]
    await s.outdoor.hello()
    owners = [uid() for _ in range(1024)]
    for owner_id in owners:
        await command(s, "audio_owner_release", owner_id=owner_id)
    for kind in ("audio_owner_acquire", "audio_owner_release"):
        fields = dict(owner_id=uid())
        if kind.endswith("acquire"):
            fields.update(purpose="enrollment", policy="cancel_active")
        with pytest.raises(OutdoorViolation, match="capacity"):
            await command(s, kind, **fields)
    result = await command(s, "audio_owner_acquire", owner_id=owners[0],
                           purpose="enrollment", policy="cancel_active")
    assert result["state"] == "denied"
    now[0] += 120
    result = await acquire(s)
    assert result["state"] == "granted"
    await s.outdoor.close()


@pytest.mark.asyncio
async def test_queued_expiry_does_not_revoke_renewed_lease():
    s = Session()
    now = [500.0]
    s.outdoor_store.clock = lambda: now[0]
    await s.outdoor.hello()
    owner = await acquire(s)
    current = s.outdoor.owner
    assert current is not None
    old_deadline = current["deadline"]
    now[0] = old_deadline - 1
    entered, release = asyncio.Event(), asyncio.Event()
    send = s.send_json_message
    async def blocked_ack(message):
        await send(message)
        if message.get("state") == "renewed":
            entered.set()
            await release.wait()
    s.send_json_message = blocked_ack
    renewal = asyncio.create_task(command(s, "audio_owner_renew",
        owner_id=owner["owner_id"], lease_id=owner["lease_id"]))
    await entered.wait()
    now[0] = old_deadline
    expiry = asyncio.create_task(s.outdoor._expire_lease(current))
    await asyncio.sleep(0)
    release.set()
    await renewal
    await expiry
    assert s.outdoor.owner is current
    assert current["deadline"] > now[0]
    await s.outdoor.close()


@pytest.mark.parametrize("trigger", ["close", "acquire"])
@pytest.mark.asyncio
async def test_drain_timeout_quarantines_device_until_all_old_work_stops(trigger):
    store = OutdoorStore()
    s = Session(store)
    s.outdoor.DRAIN_TIMEOUT = 0.005
    await s.outdoor.hello()
    started = [asyncio.Event(), asyncio.Event()]
    releases = [asyncio.Event(), asyncio.Event()]
    async def stubborn(index):
        started[index].set()
        while not releases[index].is_set():
            try:
                await releases[index].wait()
            except asyncio.CancelledError:
                pass
    tasks = [asyncio.create_task(stubborn(i)) for i in range(2)]
    s._output_tasks.update(tasks)
    await asyncio.gather(*(event.wait() for event in started))
    other = Session(store)
    try:
        with pytest.raises(OutdoorViolation, match="busy"):
            if trigger == "close":
                await s.outdoor.close()
            else:
                await acquire(s)
        with pytest.raises(OutdoorViolation, match="busy"):
            await other.outdoor.hello()
        releases[0].set()
        await tasks[0]
        await asyncio.sleep(0)
        with pytest.raises(OutdoorViolation, match="busy"):
            await other.outdoor.hello()
        releases[1].set()
        await tasks[1]
        await asyncio.sleep(0)
        assert s.authenticated_device_id not in store.connections
        await other.outdoor.hello()
    finally:
        for event in releases:
            event.set()
        await asyncio.gather(*tasks)
        await s.outdoor.close()
        await other.outdoor.close()


@pytest.mark.parametrize("returned,expected", [("ok", "ok"), ("no_speech", "no_speech"),
    ("quality_insufficient", "quality_insufficient"), ("identity_mismatch", "speaker_rejected"),
    ("private transcript", "processing_failed")])
@pytest.mark.asyncio
async def test_pipeline_outcomes_have_safe_codes_and_counted_metrics(returned, expected):
    s = Session()
    await s.outdoor.hello()
    await command(s, "conversation_resume")
    async def run(pcm):
        return returned
    s._run_pipeline = run
    await upload(s, b"\x01\x00")
    await settle(s)
    assert s.sent[-1]["code"] == expected
    assert s.outdoor_store.metrics == {expected: 1}
    assert "private transcript" not in str(s.sent)
    await s.outdoor.close()


@pytest.mark.asyncio
async def test_conversation_completion_waits_for_listening_before_terminal_result():
    s = Session()
    await s.outdoor.hello()
    await command(s, "conversation_resume")
    entered, release = asyncio.Event(), asyncio.Event()
    async def resume():
        assert s.runs == [b"\x01\x00"]
        assert s.outdoor.is_current_output()
        entered.set()
        await release.wait()
        await s.send_json_message(dict(type="state_change", state="LISTENING"))
    s._resume_listening = resume
    await upload(s, b"\x01\x00")
    try:
        await asyncio.wait_for(entered.wait(), 0.1)
        assert not any(m["type"] == "utterance_result" for m in s.sent)
    finally:
        release.set()
        await settle(s)
        await s.outdoor.close()
    assert s.sent[-2]["state"] == "LISTENING"
    assert s.sent[-1]["status"] == "completed"


@pytest.mark.parametrize("failure", ["exception", "timeout"])
@pytest.mark.asyncio
async def test_listening_failure_is_safe_and_shares_processing_deadline(failure):
    s = Session()
    s.outdoor.PROCESSING_TIMEOUT = 0.01
    await s.outdoor.hello()
    await command(s, "conversation_resume")
    async def resume():
        if failure == "exception":
            raise RuntimeError("private failure")
        await asyncio.Event().wait()
    s._resume_listening = resume
    await upload(s, b"\x01\x00")
    await settle(s)
    expected = "processing_failed" if failure == "exception" else "processing_timeout"
    assert s.sent[-1]["code"] == expected
    assert s.outdoor_store.metrics == {expected: 1}
    assert "private failure" not in str(s.sent)
    await s.outdoor.close()


@pytest.mark.asyncio
async def test_stale_worker_cannot_resume_listening_after_drain_timeout():
    s = Session()
    s.outdoor.DRAIN_TIMEOUT = 0.005
    await s.outdoor.hello()
    await command(s, "conversation_resume")
    started, release = asyncio.Event(), asyncio.Event()
    resumed = []
    async def run(pcm):
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
        return "ok"
    async def resume():
        resumed.append(True)
    s._run_pipeline = run
    s._resume_listening = resume
    await upload(s, b"\x01\x00")
    await started.wait()
    try:
        with pytest.raises(OutdoorViolation, match="busy"):
            await acquire(s)
    finally:
        release.set()
        await s.outdoor._worker
        await s.outdoor.close()
    assert not resumed
    assert not any(m.get("status") == "completed" for m in s.sent)


@pytest.mark.parametrize("threshold,expected", [(0.9, "holdout_mismatch"), (0.1, "saved")])
@pytest.mark.asyncio
async def test_enrollment_save_respects_configured_threshold_floor(threshold, expected):
    verifier = Verifier()
    verifier.threshold = threshold
    training = np.zeros(192)
    training[0] = 1
    holdout = np.zeros(192)
    holdout[:2] = [0.8, 0.6]
    vectors = iter([training, training, training, holdout])
    verifier.extract_embedding = lambda pcm: next(vectors)
    s, identity = await enrollment_session(verifier)
    ids = [await sample(s, identity) for _ in range(4)]
    result = await command(s, "enrollment_save", **identity, sample_utterance_ids=ids[:3],
                           holdout_utterance_id=ids[3])
    assert (result["state"] if expected == "saved" else result["code"]) == expected
    assert bool(verifier.saved) == (expected == "saved")
    await s.outdoor.close()


@pytest.mark.asyncio
async def test_close_retires_owner_and_keeps_replay_tombstone():
    store = OutdoorStore()
    s = Session(store)
    await s.outdoor.hello()
    owner = await acquire(s)
    await s.outdoor.close()
    other = Session(store)
    await other.outdoor.hello()
    result = await command(other, "audio_owner_acquire", owner_id=owner["owner_id"],
                           purpose="enrollment", policy="cancel_active")
    assert result["state"] == "denied"
    await other.outdoor.close()


@pytest.mark.asyncio
async def test_close_keeps_device_quarantined_until_shielded_extraction_finishes():
    verifier = Verifier()
    started, release = threading.Event(), threading.Event()
    def extract(pcm):
        started.set()
        release.wait(2)
        return np.ones(192)
    verifier.extract_embedding = extract
    s, identity = await enrollment_session(verifier)
    meta = metadata(quality_pcm())
    meta.update(purpose="enrollment", lease_id=identity["lease_id"], enrollment_id=identity["enrollment_id"])
    await upload(s, quality_pcm(), meta)
    while not started.is_set():
        await asyncio.sleep(0.001)
    other = Session(s.outdoor_store)
    try:
        await s.outdoor.close()
        with pytest.raises(OutdoorViolation, match="busy"):
            await other.outdoor.hello()
    finally:
        release.set()
        while s.authenticated_device_id in s.outdoor_store.extracting:
            await asyncio.sleep(0.001)
    await asyncio.sleep(0)
    assert s.authenticated_device_id not in s.outdoor_store.connections
    await other.outdoor.hello()
    await other.outdoor.close()


@pytest.mark.asyncio
async def test_cancelled_close_does_not_admit_device_with_live_work():
    s = Session()
    await s.outdoor.hello()
    started, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def stubborn():
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()
    task = asyncio.create_task(stubborn())
    s._output_tasks.add(task)
    await started.wait()
    closing = asyncio.create_task(s.outdoor.close())
    await cancelled.wait()
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    other = Session(s.outdoor_store)
    try:
        with pytest.raises(OutdoorViolation, match="busy"):
            await other.outdoor.hello()
    finally:
        release.set()
        await task
        await asyncio.sleep(0)
    assert s.authenticated_device_id not in s.outdoor_store.connections
    await other.outdoor.hello()
    await other.outdoor.close()


@pytest.mark.asyncio
async def test_owner_cache_capacity_is_reserved_before_cross_device_drain():
    store = OutdoorStore()
    first, second = Session(store, "first"), Session(store, "second")
    await first.outdoor.hello()
    await second.outdoor.hello()
    for _ in range(1023):
        await command(first, "audio_owner_release", owner_id=uid())
    started, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def draining():
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()
    task = asyncio.create_task(draining())
    first._output_tasks.add(task)
    await started.wait()
    acquiring = asyncio.create_task(acquire(first))
    await cancelled.wait()
    try:
        with pytest.raises(OutdoorViolation, match="capacity"):
            await acquire(second)
    finally:
        release.set()
        await acquiring
        await first.outdoor.close()
        await second.outdoor.close()


async def acquire(s, purpose="enrollment"):
    return await command(s, "audio_owner_acquire", owner_id=uid(), purpose=purpose, policy="cancel_active")


@pytest.mark.asyncio
async def test_acquire_pauses_before_await_drains_processing_and_external_output():
    s = Session()
    await s.outdoor.hello()
    await command(s, "conversation_resume")
    started = asyncio.Event()
    drained = []
    async def work(pcm=None):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            assert s.outdoor.paused
            await asyncio.sleep(0.01)
            drained.append(True)
    s._run_pipeline = work
    await upload(s, b"\x01\x00")
    await started.wait()
    external = asyncio.create_task(work())
    s._output_tasks.add(external)
    await asyncio.sleep(0)
    result = await acquire(s)
    assert result["state"] == "granted"
    assert len(drained) == 2
    assert result["output_epoch"] == 2
    assert result["conversation_paused"] is True
    assert result["lease_ms"] == 15000
    assert s.outdoor.active_task.done()
    assert external.done()
    assert not s.outdoor.output_allowed()
    await s.outdoor.close()


@pytest.mark.asyncio
async def test_release_is_idempotent_and_never_resumes_conversation():
    s = Session()
    await s.outdoor.hello()
    owner = await acquire(s)
    for _ in range(2):
        result = await command(s, "audio_owner_release", owner_id=owner["owner_id"],
                               lease_id=owner["lease_id"], reason="cancelled")
        assert result["state"] == "released"
        assert result["conversation_paused"] is True
    assert not s.outdoor.output_allowed()
    result = await command(s, "conversation_resume")
    assert result["output_epoch"] > owner["output_epoch"]
    assert s.outdoor.output_allowed()
    await s.outdoor.close()


@pytest.mark.asyncio
async def test_lease_renewal_requires_current_lease_and_expiry_stays_paused():
    s = Session()
    s.outdoor.OWNER_LEASE = 0.03
    await s.outdoor.hello()
    owner = await acquire(s)
    result = await command(s, "audio_owner_renew", owner_id=owner["owner_id"], lease_id=uid())
    assert result["state"] == "denied"
    result = await command(s, "audio_owner_renew", owner_id=owner["owner_id"], lease_id=owner["lease_id"])
    assert result["state"] == "renewed"
    await asyncio.sleep(0.06)
    assert s.outdoor.owner is None
    assert s.outdoor.paused
    await s.outdoor.close()


@pytest.mark.asyncio
async def test_resume_with_live_owner_is_denied():
    s = Session()
    await s.outdoor.hello()
    await acquire(s)
    with pytest.raises(OutdoorViolation, match="owner_required"):
        await command(s, "conversation_resume")
    assert s.outdoor.paused
    await s.outdoor.close()


@pytest.mark.asyncio
async def test_uncooperative_external_output_cannot_receive_grant():
    s = Session()
    s.outdoor.DRAIN_TIMEOUT = 0.01
    await s.outdoor.hello()
    await command(s, "conversation_resume")
    release = asyncio.Event()
    started = asyncio.Event()
    async def stubborn():
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
        assert not s.outdoor.is_current_output()
    task = asyncio.create_task(stubborn())
    s._output_tasks.add(task)
    await started.wait()
    with pytest.raises(OutdoorViolation, match="busy"):
        await acquire(s)
    assert not any(m.get("state") == "granted" for m in s.sent)
    assert s.outdoor.paused
    release.set()
    await task
    await s.outdoor.close()


@pytest.mark.asyncio
async def test_old_worker_context_remains_fenced_after_explicit_resume():
    s = Session()
    await s.outdoor.hello()
    await command(s, "conversation_resume")
    inherited = []
    async def work(pcm):
        async def later():
            await asyncio.sleep(0.04)
            inherited.append(s.outdoor.is_current_output())
        child = asyncio.create_task(later())
        s._output_tasks.add(child)
        return "ok"
    s._run_pipeline = work
    await upload(s, b"\x01\x00")
    await settle(s)
    # Capture stale context without making this child part of the drain set.
    s._output_tasks.clear()
    owner = await acquire(s)
    await command(s, "audio_owner_release", owner_id=owner["owner_id"], lease_id=owner["lease_id"])
    await command(s, "conversation_resume")
    await asyncio.sleep(0.06)
    assert inherited == [False]
    await s.outdoor.close()


class Verifier:
    def __init__(self):
        self.saved = []
        self.extracted = []
        self.deleted = 0
        self.contract = dict(profile_version=2, frontend=FRONTEND_ID,
                             model_sha256=MODEL_SHA256, embedding_dim=192)

    def _profile_contract(self):
        return dict(self.contract)

    def is_available(self):
        return True

    def is_enrolled(self, name):
        return bool(self.saved)

    def get_metadata(self, name):
        return None

    def unenroll(self, name):
        self.deleted += 1
        return True, "ok"

    def extract_embedding(self, pcm):
        self.extracted.append(pcm)
        return np.ones(192)

    def save_embedding(self, embedding, audio_bytes, name):
        # Publication must run on the event loop, never a detached thread.
        asyncio.get_running_loop()
        self.saved.append((embedding.copy(), audio_bytes, name))
        return True, "ok"


def quality_pcm():
    return (np.sin(np.arange(32000) * 0.09) * 5000).astype("<i2").tobytes()


async def enrollment_session(verifier=None):
    s = Session()
    s.speaker_verifier = verifier or Verifier()
    await s.outdoor.hello()
    owner = await acquire(s)
    identity = {k: owner[k] for k in ("owner_id", "lease_id")}
    identity["enrollment_id"] = uid()
    ack = await command(s, "enrollment_begin", **identity)
    assert ack["state"] == "collecting"
    assert ack["required_samples"] == 3
    assert ack["required_holdouts"] == 1
    return s, identity


async def sample(s, identity, pcm=None):
    pcm = quality_pcm() if pcm is None else pcm
    meta = metadata(pcm)
    meta.update(purpose="enrollment", lease_id=identity["lease_id"],
                enrollment_id=identity["enrollment_id"])
    _, ack = await upload(s, pcm, meta)
    assert ack["stage"] == "accepted"
    await settle(s)
    return meta["utterance_id"]


@pytest.mark.asyncio
async def test_enrollment_four_quality_samples_save_atomic_centroid_without_conversation():
    s, identity = await enrollment_session()
    ids = [await sample(s, identity) for _ in range(4)]
    assert len(s.speaker_verifier.extracted) == 4
    assert not s.runs
    assert not s.speaker_verifier.saved
    results = [m for m in s.sent if m["type"] == "utterance_result"]
    assert [m["utterance_id"] for m in results] == ids
    assert all(m["status"] == "completed" for m in results)
    ack = await command(s, "enrollment_save", **identity, sample_utterance_ids=ids[:3],
                        holdout_utterance_id=ids[3])
    assert ack["state"] == "saved"
    assert len(s.speaker_verifier.saved) == 1
    centroid, size, name = s.speaker_verifier.saved[0]
    assert np.linalg.norm(centroid) == pytest.approx(1)
    assert size == 64000 and name == "owner"
    await command(s, "enrollment_cancel", **identity)
    assert len(s.speaker_verifier.saved) == 1
    assert s.outdoor.paused
    await s.outdoor.close()


@pytest.mark.parametrize("policy", ["order", "duplicate", "holdout", "foreign"])
@pytest.mark.asyncio
async def test_save_rejects_wrong_order_duplicate_holdout_and_foreign_ids(policy):
    s, identity = await enrollment_session()
    ids = [await sample(s, identity) for _ in range(4)]
    training, holdout = ids[:3], ids[3]
    if policy == "order":
        training.reverse()
    elif policy == "duplicate":
        training[1] = training[0]
    elif policy == "holdout":
        holdout = training[0]
    else:
        training[0] = uid()
    ack = await command(s, "enrollment_save", **identity,
                        sample_utterance_ids=training, holdout_utterance_id=holdout)
    assert ack["state"] == "rejected"
    assert ack["code"]
    assert not s.speaker_verifier.saved
    await s.outdoor.close()


@pytest.mark.asyncio
async def test_low_quality_sample_gets_integrity_ack_then_failed_same_id_result():
    s, identity = await enrollment_session()
    utterance = await sample(s, identity, b"\x00" * 64000)
    result = s.sent[-1]
    assert result["utterance_id"] == utterance
    assert result["status"] == "failed"
    assert result["code"] == "quality_insufficient"
    assert not s.speaker_verifier.extracted
    assert not s.runs
    await s.outdoor.close()


@pytest.mark.asyncio
async def test_cancel_during_extract_fences_shielded_thread_completion():
    verifier = Verifier()
    started, release = threading.Event(), threading.Event()
    def extract(pcm):
        started.set()
        release.wait(2)
        return np.ones(192)
    verifier.extract_embedding = extract
    s, identity = await enrollment_session(verifier)
    meta = metadata(quality_pcm())
    meta.update(purpose="enrollment", lease_id=identity["lease_id"], enrollment_id=identity["enrollment_id"])
    await upload(s, quality_pcm(), meta)
    while not started.is_set():
        await asyncio.sleep(0.001)
    await command(s, "enrollment_cancel", **identity)
    release.set()
    await asyncio.sleep(0.03)
    assert not s.speaker_verifier.saved
    assert not any(m.get("status") == "completed" for m in s.sent)
    assert not s.runs
    await s.outdoor.close()


@pytest.mark.asyncio
async def test_changed_frontend_during_extract_never_credits_sample():
    verifier = Verifier()
    def extract(pcm):
        verifier.contract["frontend"] = "wrong"
        return np.ones(192)
    verifier.extract_embedding = extract
    s, identity = await enrollment_session(verifier)
    await sample(s, identity)
    assert s.sent[-1]["status"] == "failed"
    assert s.sent[-1]["code"] == "model_provenance"
    assert not s.speaker_verifier.saved
    await s.outdoor.close()


@pytest.mark.asyncio
async def test_profile_controls_require_active_enrollment_owner_not_client_owner_fields():
    s = Session()
    s.speaker_verifier = Verifier()
    await s.outdoor.hello()
    result = await command(s, "voiceprint_unenroll")
    assert result["success"] is False
    assert s.speaker_verifier.deleted == 0
    owner = await acquire(s, "diagnostic")
    result = await command(s, "voiceprint_unenroll")
    assert result["success"] is False
    await command(s, "audio_owner_release", owner_id=owner["owner_id"], lease_id=owner["lease_id"])
    await acquire(s)
    query = await command(s, "voiceprint_query")
    assert query["available"] is True and query["is_enrolled"] is False
    result = await command(s, "voiceprint_unenroll")
    assert result["success"] is True
    assert s.speaker_verifier.deleted == 1
    await s.outdoor.close()


@pytest.mark.asyncio
async def test_diagnostic_lease_cannot_upload_or_begin_enrollment():
    s = Session()
    s.speaker_verifier = Verifier()
    await s.outdoor.hello()
    owner = await acquire(s, "diagnostic")
    identity = dict(owner_id=owner["owner_id"], lease_id=owner["lease_id"], enrollment_id=uid())
    result = await command(s, "enrollment_begin", **identity)
    assert result["state"] == "rejected"
    meta = metadata(quality_pcm())
    meta.update(purpose="enrollment", lease_id=owner["lease_id"], enrollment_id=identity["enrollment_id"])
    result = await command(s, "utterance_begin", **meta)
    assert result["code"] == "owner_required"
    assert not s.runs
    await s.outdoor.close()


@pytest.mark.parametrize("case", ["checksum", "length"])
@pytest.mark.asyncio
async def test_failed_integrity_never_dispatches(case):
    s = Session()
    await s.outdoor.hello()
    await command(s, "conversation_resume")
    meta = metadata(b"\x01\x00" * 2)
    await command(s, "utterance_begin", **meta)
    pcm = b"\x02\x00" * 2 if case == "checksum" else b"\x01\x00"
    await s.outdoor.binary(frame(meta["utterance_id"], 0, pcm))
    result = await command(s, "utterance_commit", **{k: meta[k] for k in ("utterance_id", "total_samples", "sha256")})
    assert result["code"] == ("checksum_mismatch" if case == "checksum" else "invalid_length")
    assert not s.runs
    assert s.outdoor.assembly is None
    await s.outdoor.close()


@pytest.mark.parametrize("kind", [[], None, 12])
@pytest.mark.asyncio
async def test_non_string_control_type_rejected_safely(kind):
    s = Session()
    await s.outdoor.hello()
    with pytest.raises(OutdoorViolation, match="invalid_message"):
        await s.outdoor.control(dict(type=kind, connection_id=s.outdoor.connection_id, request_id=uid()))
    await s.outdoor.close()


@pytest.mark.asyncio
async def test_processing_failure_status_is_rejected_without_private_code():
    s = Session()
    await s.outdoor.hello()
    await command(s, "conversation_resume")
    async def run(pcm):
        return "private transcript accidentally returned"
    s._run_pipeline = run
    meta, _ = await upload(s, b"\x01\x00")
    await settle(s)
    assert s.sent[-1]["code"] == "processing_failed"
    result = await command(s, "utterance_status", utterance_id=meta["utterance_id"])
    assert result["stage"] == "rejected"
    assert "private" not in str(s.sent)
    await s.outdoor.close()


@pytest.mark.asyncio
async def test_cancel_of_completed_sample_revokes_enrollment_credit():
    s, identity = await enrollment_session()
    ids = [await sample(s, identity) for _ in range(4)]
    await command(s, "utterance_cancel", utterance_id=ids[0], reason="cancelled")
    result = await command(s, "enrollment_save", **identity, sample_utterance_ids=ids[:3],
                           holdout_utterance_id=ids[3])
    assert result["state"] == "rejected"
    assert not s.speaker_verifier.saved
    await s.outdoor.close()


@pytest.mark.asyncio
async def test_disconnect_during_integrity_ack_never_starts_runner():
    s = Session()
    await s.outdoor.hello()
    await command(s, "conversation_resume")
    original = s.send_json_message
    async def send(message):
        await original(message)
        if message.get("stage") == "accepted":
            await s.outdoor.close()
    s.send_json_message = send
    meta, _ = await upload(s, b"\x01\x00")
    await settle(s)
    assert not s.runs
    assert s.outdoor_store.ledger[(s.authenticated_device_id, meta["utterance_id"])]["stage"] == "cancelled"


@pytest.mark.asyncio
async def test_new_utterance_is_refused_when_external_output_is_registered():
    s = Session()
    await s.outdoor.hello()
    await command(s, "conversation_resume")
    task = asyncio.create_task(asyncio.sleep(1))
    s._output_tasks.add(task)
    result = await command(s, "utterance_begin", **metadata(b"\x01\x00"))
    assert result["code"] == "busy"
    await s.outdoor.close()


@pytest.mark.asyncio
async def test_control_json_size_bound_applies_to_ignored_optional_fields():
    s = Session()
    await s.outdoor.hello()
    with pytest.raises(OutdoorViolation, match="invalid_message"):
        await command(s, "conversation_resume", optional="x" * 4096)
    assert s.outdoor.paused
    await s.outdoor.close()


@pytest.mark.asyncio
async def test_cancelled_extraction_cannot_accumulate_detached_threads():
    verifier = Verifier()
    started, release = threading.Event(), threading.Event()
    def extract(pcm):
        started.set()
        release.wait(2)
        return np.ones(192)
    verifier.extract_embedding = extract
    s, identity = await enrollment_session(verifier)
    meta = metadata(quality_pcm())
    meta.update(purpose="enrollment", lease_id=identity["lease_id"], enrollment_id=identity["enrollment_id"])
    await upload(s, quality_pcm(), meta)
    while not started.is_set():
        await asyncio.sleep(0.001)
    await command(s, "enrollment_cancel", **identity)
    identity["enrollment_id"] = uid()
    await command(s, "enrollment_begin", **identity)
    meta["utterance_id"] = uid()
    meta["enrollment_id"] = identity["enrollment_id"]
    result = await command(s, "utterance_begin", **meta)
    release.set()
    assert result["code"] == "busy"
    await s.outdoor.close()


@pytest.mark.asyncio
async def test_save_revalidates_lease_provenance_and_sample_consistency():
    verifier = Verifier()
    vectors = iter([np.ones(192), np.ones(192), np.ones(192), -np.ones(192)])
    verifier.extract_embedding = lambda pcm: next(vectors)
    s, identity = await enrollment_session(verifier)
    ids = [await sample(s, identity) for _ in range(4)]
    result = await command(s, "enrollment_save", **identity, sample_utterance_ids=ids[:3],
                           holdout_utterance_id=ids[3])
    assert result["code"] == "holdout_mismatch"
    assert not verifier.saved
    verifier.contract["model_sha256"] = "unvalidated-injected-session"
    result = await command(s, "enrollment_save", **identity, sample_utterance_ids=ids[:3],
                           holdout_utterance_id=ids[3])
    assert result["code"] == "model_provenance"
    s.outdoor.owner["deadline"] = 0
    result = await command(s, "enrollment_save", **identity, sample_utterance_ids=ids[:3],
                           holdout_utterance_id=ids[3])
    assert result["code"] == "owner_required"
    assert not verifier.saved
    await s.outdoor.close()


@pytest.mark.asyncio
async def test_processing_deadline_includes_integrity_ack_latency():
    s = Session()
    s.outdoor.PROCESSING_TIMEOUT = 0.01
    await s.outdoor.hello()
    await command(s, "conversation_resume")
    send = s.send_json_message
    async def delayed(message):
        await send(message)
        if message.get("stage") == "accepted":
            await asyncio.sleep(0.03)
    s.send_json_message = delayed
    await upload(s, b"\x01\x00")
    await settle(s)
    assert not s.runs
    assert s.sent[-1]["code"] == "processing_timeout"
    assert s.outdoor_store.metrics == {"processing_timeout": 1}
    await s.outdoor.close()


def uid():
    return str(uuid.uuid4())


class Session:
    def __init__(self, store=None, device="device"):
        self.authenticated_device_id = device
        self.session_id = "fixture"
        self.ready = True
        self.speaker_verifier: Any = None
        self.sent = []
        self.runs = []
        self._output_tasks = set()
        self.outdoor_store = store or OutdoorStore()
        self.outdoor = OutdoorSession(self)

    async def send_json_message(self, message):
        self.sent.append(dict(message))

    async def _run_pipeline(self, captured):
        self.runs.append(captured)
        return "ok"

    async def _resume_listening(self):
        pass


async def command(s, kind, **fields):
    payload = dict(type=kind, connection_id=s.outdoor.connection_id, request_id=uid(), **fields)
    await s.outdoor.control(payload)
    return s.sent[-1]


@pytest.mark.asyncio
async def test_hello_is_paused_with_exact_android_limits():
    s = Session()
    hello = await s.outdoor.hello()
    assert hello["protocol_version"] == "2.0"
    assert hello["conversation_paused"] is True
    assert hello["limits"] == dict(max_samples=320000, max_chunk_bytes=16384,
                                   retry_ttl_ms=30000, owner_lease_ms=15000)
    assert set(hello["capabilities"]) == {"utterance_upload_v2", "exclusive_audio_v1"}
    assert str(uuid.UUID(hello["connection_id"])) == hello["connection_id"]
    assert hello["server_instance_id"] == s.outdoor_store.server_instance_id
    assert not s.outdoor.output_allowed()
    await s.outdoor.close()


@pytest.mark.asyncio
async def test_single_live_connection_per_authenticated_device():
    store = OutdoorStore()
    first, second = Session(store), Session(store)
    await first.outdoor.hello()
    with pytest.raises(OutdoorViolation, match="busy"):
        await second.outdoor.hello()
    await first.outdoor.close()
    await second.outdoor.hello()
    await second.outdoor.close()


@pytest.mark.asyncio
async def test_explicit_resume_increments_epoch_and_echoes_identity():
    s = Session()
    await s.outdoor.hello()
    result = await command(s, "conversation_resume")
    assert result["type"] == "conversation_resumed"
    assert result["output_epoch"] == 1
    assert result["connection_id"] == s.outdoor.connection_id
    assert str(uuid.UUID(result["request_id"])) == result["request_id"]
    assert s.outdoor.output_allowed()
    await s.outdoor.close()


@pytest.mark.parametrize("field,value", [("connection_id", uid()), ("request_id", "BAD"),
                                            ("request_id", True), ("type", "unknown")])
@pytest.mark.asyncio
async def test_invalid_common_envelope_is_protocol_failure(field, value):
    s = Session()
    await s.outdoor.hello()
    payload = dict(type="conversation_resume", connection_id=s.outdoor.connection_id,
                   request_id=uid())
    payload[field] = value
    with pytest.raises(OutdoorViolation):
        await s.outdoor.control(payload)
    assert s.outdoor.paused
    await s.outdoor.close()
