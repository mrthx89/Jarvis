"""Independent upload deadlines and cancellation identity retention."""
import asyncio

import pytest

from nexo_server.gateway.outdoor import OutdoorViolation
from tests.test_outdoor_runtime import Session, command, frame, metadata, uid


@pytest.mark.parametrize("traffic", ["progress", "duplicate", "invalid", "begin", "commit_after_gap"])
@pytest.mark.asyncio
async def test_only_progress_refreshes_inactivity_and_never_extends_wall_deadline(traffic):
    s = Session()
    now = [100.0]
    s.outdoor_store.clock = lambda: now[0]
    await s.outdoor.hello()
    await command(s, "conversation_resume")
    meta = metadata(b"\x01\x00" * 20)
    try:
        await command(s, "utterance_begin", **meta)
        await s.outdoor.binary(frame(meta["utterance_id"], 0, b"\x01\x00"))
        entry = s.outdoor.assembly
        now[0] = 104.0
        if traffic == "progress":
            await s.outdoor.binary(frame(meta["utterance_id"], 1, b"\x01\x00"))
            now[0] = 105.0
            await s.outdoor._upload_expired(entry)
            assert s.outdoor.assembly is entry
            for offset in range(2, 11):
                now[0] += 3
                await s.outdoor.binary(frame(meta["utterance_id"], offset, b"\x01\x00"))
            assert s.outdoor.assembly is None
        elif traffic == "commit_after_gap":
            now[0] = 105.0
            await command(s, "utterance_commit", **{k: meta[k] for k in
                          ("utterance_id", "sha256", "total_samples")})
        else:
            if traffic == "duplicate":
                await s.outdoor.binary(frame(meta["utterance_id"], 0, b"\x01\x00"))
            elif traffic == "invalid":
                with pytest.raises(OutdoorViolation, match="sequence_gap"):
                    await s.outdoor.binary(frame(meta["utterance_id"], 2, b"\x01\x00"))
            else:
                await command(s, "utterance_begin", **meta)
            now[0] = 105.0
            await s.outdoor._upload_expired(entry)
        assert s.outdoor.assembly is None and not s.runs
        assert s.sent[-1]["code"] == "upload_timeout"
    finally:
        await s.outdoor.close()


@pytest.mark.parametrize("scope", ["device", "global"])
@pytest.mark.asyncio
async def test_unknown_cancel_respects_ledger_capacity_without_false_cancelled_ack(scope):
    s = Session()
    await s.outdoor.hello()
    try:
        for i in range(128 if scope == "device" else 512):
            device = s.authenticated_device_id if scope == "device" else str(i)
            s.outdoor_store.ledger[(device, uid())] = dict(stage="completed", terminal_at=s.outdoor_store.clock())
        result = await command(s, "utterance_cancel", utterance_id=uid())
        assert result["type"] == "utterance_error" and result["code"] == "capacity"
    finally:
        await s.outdoor.close()


@pytest.mark.asyncio
async def test_cancel_tombstone_ttl_is_not_extended_by_retries():
    s = Session()
    now = [100.0]
    s.outdoor_store.clock = lambda: now[0]
    await s.outdoor.hello()
    await command(s, "conversation_resume")
    u = uid()
    try:
        await command(s, "utterance_cancel", utterance_id=u)
        now[0] = 219.999
        assert (await command(s, "utterance_cancel", utterance_id=u))["stage"] == "cancelled"
        now[0] = 220.0
        assert (await command(s, "utterance_status", utterance_id=u))["stage"] == "unknown"
    finally:
        await s.outdoor.close()


@pytest.mark.asyncio
async def test_upload_inactivity_expires_without_partial_dispatch():
    s = Session()
    s.outdoor.INACTIVITY_TIMEOUT = 0.02
    await s.outdoor.hello()
    await command(s, "conversation_resume")
    try:
        meta = metadata(b"\x01\x00" * 10)
        await command(s, "utterance_begin", **meta)
        await s.outdoor.binary(frame(meta["utterance_id"], 0, b"\x01\x00"))
        await asyncio.sleep(0.06)
        assert s.outdoor.assembly is None
        assert s.sent[-1]["code"] == "upload_timeout"
        assert s.outdoor.paused and not s.runs
    finally:
        await s.outdoor.close()


@pytest.mark.asyncio
async def test_cancel_before_begin_remains_terminal_across_reconnect_and_binds_metadata():
    s = Session()
    await s.outdoor.hello()
    await command(s, "conversation_resume")
    meta = metadata(b"\x01\x00")
    try:
        await command(s, "utterance_cancel", utterance_id=meta["utterance_id"], reason="cancelled")
        assert (await command(s, "utterance_status", utterance_id=meta["utterance_id"]))["stage"] == "cancelled"
        await s.outdoor.close()
        s = Session(s.outdoor_store)
        await s.outdoor.hello()
        await command(s, "conversation_resume")
        result = await command(s, "utterance_begin", **meta)
        assert result["stage"] == "cancelled"
        assert result["total_samples"] == meta["total_samples"]
        assert result["sha256"] == meta["sha256"]
        assert s.outdoor.assembly is None and not s.runs
        result = await command(s, "utterance_begin", **dict(meta, total_samples=2))
        assert result["code"] == "id_conflict"
    finally:
        await s.outdoor.close()
