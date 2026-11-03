"""Enrollment publication results survive volatile capture/lease teardown."""
import asyncio

import pytest

from tests.test_outdoor_runtime import (
    Session, command, enrollment_session, sample, uid,
)


@pytest.mark.parametrize("scope", ["device", "global"])
@pytest.mark.asyncio
async def test_enrollment_terminal_capacity_and_ttl_do_not_evict_young_results(scope):
    s, identity = await enrollment_session()
    now = [100.0]
    s.outdoor_store.clock = lambda: now[0]
    try:
        await command(s, "enrollment_cancel", **identity)
        limit = 128 if scope == "device" else 512
        for i in range(limit - 1):
            device = s.authenticated_device_id if scope == "device" else str(i)
            s.outdoor_store.enrollments[(device, uid())] = dict(
                owner_id=uid(), lease_id=uid(), result=dict(state="cancelled"), terminal_at=100.0)
        other = dict(identity, enrollment_id=uid())
        result = await command(s, "enrollment_begin", **other)
        assert result["state"] == "rejected" and result["code"] == "capacity"
        assert (await command(s, "enrollment_begin", **identity))["state"] == "cancelled"
        now[0] = 220.0
        s.outdoor.owner["deadline"] = 1000.0
        result = await command(s, "enrollment_begin", **other)
        assert result["state"] == "collecting"
        assert len(s.outdoor_store.enrollments) == 1
    finally:
        await s.outdoor.close()


@pytest.mark.parametrize("exit_kind", ["release", "expiry", "reconnect"])
@pytest.mark.asyncio
async def test_exit_before_save_keeps_cancelled_terminal_result(exit_kind):
    s, identity = await enrollment_session()
    verifier = s.speaker_verifier
    try:
        ids = [await sample(s, identity) for _ in range(4)]
        if exit_kind == "release":
            await command(s, "audio_owner_release", owner_id=identity["owner_id"], lease_id=identity["lease_id"])
        elif exit_kind == "expiry":
            s.outdoor.owner["deadline"] = 0
            await s.outdoor._expire_lease(s.outdoor.owner)
        else:
            await s.outdoor.close()
            s = Session(s.outdoor_store)
            s.speaker_verifier = verifier
            await s.outdoor.hello()
        result = await command(s, "enrollment_save", **identity,
                               sample_utterance_ids=ids[:3], holdout_utterance_id=ids[3])
        assert result["state"] == "cancelled"
        assert not verifier.saved
    finally:
        await s.outdoor.close()


@pytest.mark.asyncio
async def test_enrollment_terminal_identity_cannot_be_rebound_to_new_lease():
    s, identity = await enrollment_session()
    try:
        await command(s, "enrollment_cancel", **identity)
        result = await command(s, "enrollment_begin", **dict(identity, lease_id=uid()))
        assert result["state"] == "rejected" and result["code"] == "id_conflict"
    finally:
        await s.outdoor.close()



@pytest.mark.parametrize("exit_kind", ["cancel", "release", "expiry", "reconnect"])
@pytest.mark.asyncio
async def test_saved_result_is_replayed_after_exit_without_republication(exit_kind):
    s, identity = await enrollment_session()
    verifier = s.speaker_verifier
    ids = [await sample(s, identity) for _ in range(4)]
    fields = dict(**identity, sample_utterance_ids=ids[:3], holdout_utterance_id=ids[3])
    try:
        saved = await command(s, "enrollment_save", **fields)
        assert saved["state"] == "saved"
        publications = len(verifier.saved)
        if exit_kind == "cancel":
            result = await command(s, "enrollment_cancel", **identity)
            assert result["state"] == "saved"
        elif exit_kind == "release":
            await command(s, "audio_owner_release", owner_id=identity["owner_id"],
                          lease_id=identity["lease_id"])
        elif exit_kind == "expiry":
            s.outdoor.owner["deadline"] = 0
            await s.outdoor._expire_lease(s.outdoor.owner)
        else:
            await s.outdoor.close()
            s = Session(s.outdoor_store)
            s.speaker_verifier = verifier
            await s.outdoor.hello()
        replay = await command(s, "enrollment_save", **fields)
        for key in ("state", "code", "enrollment_id"):
            assert replay[key] == saved[key]
        assert len(verifier.saved) == publications
        result = await command(s, "enrollment_begin", **identity)
        assert result["state"] == "saved"
        assert s.outdoor.enrollment is None
    finally:
        await s.outdoor.close()


@pytest.mark.parametrize("first", ["save", "cancel"])
@pytest.mark.asyncio
async def test_save_cancel_linearization_has_one_terminal_result(first):
    s, identity = await enrollment_session()
    try:
        ids = [await sample(s, identity) for _ in range(4)]
        fields = dict(**identity, sample_utterance_ids=ids[:3], holdout_utterance_id=ids[3])
        entered, release = asyncio.Event(), asyncio.Event()
        original = s.send_json_message
        async def held_ack(message):
            await original(message)
            if message.get("state") in {"saved", "cancelled"} and not entered.is_set():
                entered.set()
                await release.wait()
        s.send_json_message = held_ack
        first_task = asyncio.create_task(command(s, "enrollment_" + first,
            **(fields if first == "save" else identity)))
        await asyncio.wait_for(entered.wait(), 1)
        second_task = asyncio.create_task(command(s, "enrollment_" + ("cancel" if first == "save" else "save"),
            **(identity if first == "save" else fields)))
        release.set()
        await asyncio.gather(first_task, second_task)
        terminal = [m["state"] for m in s.sent if m.get("type") == "enrollment_ack"
                    and m.get("state") != "collecting"]
        expected = "saved" if first == "save" else "cancelled"
        assert terminal == [expected, expected]
        assert bool(s.speaker_verifier.saved) == (first == "save")
        assert (await command(s, "enrollment_begin", **identity))["state"] == expected
    finally:
        await s.outdoor.close()
