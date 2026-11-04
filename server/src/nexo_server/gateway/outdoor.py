"""Whole-utterance transport and exclusive audio ownership (v2 only).

Inject one OutdoorStore per process. The adapter must translate OutdoorViolation
into its ProtocolViolation and send controls even when conversation output is
fenced. Gate pipeline JSON/binary with is_current_output(); gate new external
speech with output_allowed() and register its task in session._output_tasks.
"""
import asyncio
from collections import Counter
from contextvars import ContextVar
import hashlib
import json
from typing import Any
import struct
import time
import uuid

from ..pipeline.enrollment_quality import assess_pcm, normalize_embedding, prepare_profile
from ..pipeline.speaker import DEFAULT_THRESHOLD, FRONTEND_ID, MODEL_SHA256


class OutdoorViolation(ValueError):
    """Fixed safe protocol diagnostic, translated by the gateway adapter."""


class OutdoorStore:
    def __init__(self):
        self.server_instance_id = str(uuid.uuid4())
        self.connections: dict[str, Any] = {}
        self.ledger: dict[tuple[str, str], dict[str, Any]] = {}
        self.owners: dict[tuple[str, str], dict[str, Any]] = {}
        self.enrollments: dict[tuple[str, str], dict[str, Any]] = {}
        self.metrics = Counter()
        self.extracting = set()
        self.clock = time.monotonic

    def prune(self):
        now = self.clock()
        for key, entry in list(self.ledger.items()):
            if "terminal_at" in entry and now - entry["terminal_at"] >= 120:
                del self.ledger[key]
        for key, entry in list(self.owners.items()):
            if "released_at" in entry and now - entry["released_at"] >= 120:
                del self.owners[key]
        for key, entry in list(self.enrollments.items()):
            if "terminal_at" in entry and now - entry["terminal_at"] >= 120:
                del self.enrollments[key]

    def has_capacity(self, device):
        self.prune()
        return len(self.ledger) < 512 and sum(k[0] == device for k in self.ledger) < 128


class OutdoorSession:
    MAX_SAMPLES = 320000
    MAX_CHUNK_BYTES = 16384
    UPLOAD_TIMEOUT = 45.0
    INACTIVITY_TIMEOUT = 15.0
    PROCESSING_TIMEOUT = 120.0
    DRAIN_TIMEOUT = 3.0
    OWNER_LEASE = 15.0

    def __init__(self, session: Any, store=None):
        self.session = session
        self.store = store or session.outdoor_store
        self.device = session.authenticated_device_id
        self.connection_id = str(uuid.uuid4())
        self.epoch = 0
        self.paused = True
        self.closed = False
        self.active_task = None
        self.assembly = None
        self._output_generation: ContextVar[int | None] = ContextVar("outdoor_generation", default=None)
        self.output_utterance: ContextVar[str | None] = ContextVar("outdoor_utterance", default=None)
        self.generation = 0
        self._upload_handle = None
        self._worker = None
        self._active_entry = None
        self._tasks = set()
        self._quarantined = set()
        self._extractions = set()
        self.owner: dict[str, Any] | None = None
        self.enrollment: dict[str, Any] | None = None
        self._lease_handle = None
        self._control_lock = asyncio.Lock()

    def _spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task):
        self._tasks.discard(task)
        if not task.cancelled():
            task.exception()

    def is_current_output(self):
        captured = self._output_generation.get()
        return self.output_allowed() and (captured is None or captured == self.generation)

    def _terminal(self, entry, stage):
        entry.pop("pcm", None)
        entry.update(stage=stage, terminal_at=self.store.clock())

    def _discard_assembly(self):
        if self._upload_handle:
            self._upload_handle.cancel()
            self._upload_handle = None
        if self.assembly is not None:
            self.store.ledger.pop((self.device, self.assembly["utterance_id"]), None)
            self.assembly.pop("pcm", None)
            self.assembly = None

    def _arm_upload(self, entry):
        if self._upload_handle:
            self._upload_handle.cancel()
        deadline = min(entry["wall_deadline"], entry["progress_deadline"])
        self._upload_handle = asyncio.get_running_loop().call_later(
            max(0, deadline - self.store.clock()),
            lambda: self._spawn(self._upload_expired(entry)))

    async def _check_upload_deadline(self, entry):
        if self.store.clock() < min(entry["wall_deadline"], entry["progress_deadline"]):
            return False
        self.paused = True
        self.epoch += 1
        self._discard_assembly()
        await self._error(entry["request"], entry["utterance_id"], "upload_timeout")
        return True

    async def _upload_expired(self, entry):
        async with self._control_lock:
            if self.assembly is entry and not await self._check_upload_deadline(entry):
                self._arm_upload(entry)

    def _cleanup_registration(self, task=None):
        if task is not None:
            self._quarantined.discard(task)
            if not task.cancelled():
                task.exception()
        if (self.closed and not self._quarantined and not self._extractions
                and self.store.connections.get(self.device) is self):
            del self.store.connections[self.device]

    async def _drain(self):
        self.generation += 1
        for listener in getattr(self.session, "_output_boundary_listeners", ()):
            listener()
        self._discard_assembly()
        if self._active_entry and self._active_entry["stage"] == "accepted":
            self._terminal(self._active_entry, "cancelled")
        tasks = {t for t in (self.active_task, self._worker, *getattr(self.session, "_output_tasks", ()))
                 if t is not None and not t.done() and t is not asyncio.current_task()}
        for task in tasks:
            if task not in self._quarantined:
                self._quarantined.add(task)
                task.add_done_callback(self._cleanup_registration)
            task.cancel()
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=self.DRAIN_TIMEOUT)
            if pending:
                self.paused = True
                self.closed = True
                raise OutdoorViolation("busy")
        # Only reset shared playback state after every previous writer is drained.
        self.session._tts_started = False
        self.session._playback_until = 0.0
        self.session._cooldown_until = 0.0

    async def hello(self) -> dict[str, Any]:
        other = self.store.connections.get(self.device)
        if other is not None and other is not self:
            raise OutdoorViolation("busy")
        if self.closed or not self.device:
            raise OutdoorViolation("invalid_message")
        self.store.connections[self.device] = self
        return dict(type="server_hello", session_id=self.session.session_id,
                    status="IDLE", protocol_version="2.0",
                    capabilities=["utterance_upload_v2", "exclusive_audio_v1"],
                    connection_id=self.connection_id,
                    server_instance_id=self.store.server_instance_id,
                    output_epoch=self.epoch, conversation_paused=True,
                    limits=dict(max_samples=320000, max_chunk_bytes=16384,
                                retry_ttl_ms=30000, owner_lease_ms=15000))

    def output_allowed(self):
        return not self.closed and not self.paused

    def busy(self, owned_task=None):
        return (self.device in self.store.extracting or self.assembly is not None
                or self._active_entry is not None and self._active_entry["stage"] == "accepted"
                or any(task is not None and task is not owned_task and not task.done()
                       for task in (self.active_task, self._worker,
                                    *getattr(self.session, "_output_tasks", ()))))

    @staticmethod
    def _uuid(value):
        try:
            if type(value) is not str or str(uuid.UUID(value)) != value:
                raise ValueError
        except (ValueError, AttributeError):
            raise OutdoorViolation("invalid_message") from None
        return value

    async def _send(self, kind, request_id, **fields):
        await self.session.send_json_message(dict(type=kind, request_id=request_id,
            connection_id=self.connection_id, output_epoch=self.epoch, **fields))

    async def control(self, payload):
        async with self._control_lock:
            await self._control(payload)

    async def _control(self, payload):
        if type(payload) is not dict or self.closed or type(payload.get("type")) is not str:
            raise OutdoorViolation("invalid_message")
        try:
            if len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 4096:
                raise OutdoorViolation("invalid_message")
        except (TypeError, ValueError, UnicodeError):
            raise OutdoorViolation("invalid_message") from None
        request = self._uuid(payload.get("request_id"))
        if self._uuid(payload.get("connection_id")) != self.connection_id:
            raise OutdoorViolation("stale_connection")
        if self.store.connections.get(self.device) is not self:
            raise OutdoorViolation("stale_connection")
        if payload.get("type") == "conversation_resume":
            if self.owner is not None:
                raise OutdoorViolation("owner_required")
            await self._drain()
            self.epoch += 1
            self.paused = False
            await self._send("conversation_resumed", request, conversation_paused=False)
        elif payload.get("type", "").startswith("audio_owner_"):
            await self._ownership(payload, request)
        elif payload.get("type", "").startswith("utterance_"):
            await self._utterance(payload, request)
        elif payload.get("type", "").startswith("enrollment_"):
            await self._enrollment_control(payload, request)
        elif payload.get("type") in ("voiceprint_query", "voiceprint_unenroll"):
            await self._profile_control(payload, request)
        else:
            raise OutdoorViolation("invalid_message")

    def _owner_valid(self, p=None, purpose=None):
        owner = self.owner
        return bool(owner and self.store.clock() < owner["deadline"]
                    and (purpose is None or owner["purpose"] == purpose)
                    and (p is None or p.get("owner_id") == owner["owner_id"]
                         and p.get("lease_id") == owner["lease_id"]))

    def _arm_lease(self):
        if self._lease_handle:
            self._lease_handle.cancel()
        self.owner["deadline"] = self.store.clock() + self.OWNER_LEASE
        owner = self.owner
        self._lease_handle = asyncio.get_running_loop().call_later(
            self.OWNER_LEASE, lambda: self._spawn(self._expire_lease(owner)))

    async def _expire_lease(self, owner):
        async with self._control_lock:
            if self.owner is owner and self.store.clock() >= owner["deadline"]:
                self._retire_owner()
                self._discard_enrollment()
                self.paused = True
                self.epoch += 1
                await self._drain()

    def _retire_owner(self):
        if self.owner is not None:
            self.store.owners[(self.device, self.owner["owner_id"])] = {
                "released_at": self.store.clock()}
            self.owner = None

    async def _ownership(self, p, request):
        self.store.prune()
        owner_id = self._uuid(p.get("owner_id"))
        kind = p["type"]
        if "lease_id" in p:
            self._uuid(p["lease_id"])
        fields: dict[str, Any] = dict(owner_id=owner_id, conversation_paused=True)
        if kind == "audio_owner_acquire":
            if p.get("purpose") not in ("enrollment", "diagnostic") or p.get("policy") != "cancel_active":
                raise OutdoorViolation("invalid_message")
            if "released_at" in self.store.owners.get((self.device, owner_id), {}):
                await self._send("audio_owner_ack", request, **fields, state="denied", code="owner_required")
                return
            if self.owner is not None:
                if not self._owner_valid() or self.owner["owner_id"] != owner_id or self.owner["purpose"] != p["purpose"]:
                    raise OutdoorViolation("busy")
                fields.update(state="granted", lease_id=self.owner["lease_id"],
                              lease_ms=max(1, int(self.OWNER_LEASE*1000)))
                await self._send("audio_owner_ack", request, **fields)
                return
            if (self.device, owner_id) not in self.store.owners and len(self.store.owners) >= 1024:
                raise OutdoorViolation("capacity")
            self.paused = True
            self.epoch += 1
            self._discard_enrollment()
            self.store.owners[(self.device, owner_id)] = {}
            try:
                await self._drain()
                if self.closed:
                    raise OutdoorViolation("busy")
            except BaseException:
                self.store.owners[(self.device, owner_id)] = {"released_at": self.store.clock()}
                raise
            self.owner = dict(owner_id=owner_id, lease_id=str(uuid.uuid4()), purpose=p["purpose"])
            self._arm_lease()
            self.store.owners[(self.device, owner_id)] = self.owner
            fields.update(state="granted", lease_id=self.owner["lease_id"],
                          lease_ms=max(1, int(self.OWNER_LEASE*1000)))
        elif kind == "audio_owner_renew":
            if self._owner_valid(p):
                self._arm_lease()
                fields.update(state="renewed", lease_id=self.owner["lease_id"],
                              lease_ms=max(1, int(self.OWNER_LEASE*1000)))
            else:
                fields.update(state="denied", code="owner_required")
        elif kind == "audio_owner_release":
            if self.owner is not None and (owner_id != self.owner["owner_id"]
                    or "lease_id" in p and p["lease_id"] != self.owner["lease_id"]):
                fields.update(state="denied", code="owner_required")
            else:
                if (self.device, owner_id) not in self.store.owners and len(self.store.owners) >= 1024:
                    raise OutdoorViolation("capacity")
                self.paused = True
                self._retire_owner()
                self.store.owners.setdefault((self.device, owner_id), {"released_at": self.store.clock()})
                self._discard_enrollment()
                if self._lease_handle:
                    self._lease_handle.cancel()
                await self._drain()
                fields.update(state="released")
        else:
            raise OutdoorViolation("invalid_message")
        await self._send("audio_owner_ack", request, **fields)

    @staticmethod
    def _contract(verifier):
        contract = dict(verifier._profile_contract())
        if contract != dict(profile_version=2, frontend=FRONTEND_ID,
                            model_sha256=MODEL_SHA256, embedding_dim=192):
            raise OutdoorViolation("model_provenance")
        return contract

    async def _profile_control(self, p, request):
        verifier = self.session.speaker_verifier
        allowed = self._owner_valid(purpose="enrollment")
        available = bool(allowed and verifier and verifier.is_available())
        success = allowed
        if p["type"] == "voiceprint_unenroll":
            success = False
            if available:
                self._discard_enrollment()
                await self._drain()
                success, _ = verifier.unenroll("owner")
        await self._send("voiceprint_dialog", request,
            action="query" if p["type"] == "voiceprint_query" else "unenroll",
            available=available, is_enrolled=bool(available and verifier.is_enrolled("owner")),
            success=bool(success), code="ok" if success else "owner_required",
            metadata=verifier.get_metadata("owner") if available else None)

    def _discard_enrollment(self):
        if self.enrollment is not None:
            entry = self.store.enrollments[(self.device, self.enrollment["enrollment_id"])]
            if "result" not in entry:
                entry.update(result=dict(state="cancelled"), terminal_at=self.store.clock())
            self.enrollment["samples"].clear()
            self.enrollment["order"].clear()
            self.enrollment = None

    async def _enrollment_control(self, p, request):
        identity = {k: self._uuid(p.get(k)) for k in ("owner_id", "lease_id", "enrollment_id")}
        async def reply(state, **fields):
            await self._send("enrollment_ack", request, **identity, state=state, **fields)
        self.store.prune()
        key = (self.device, identity["enrollment_id"])
        record = self.store.enrollments.get(key)
        if record and any(record[k] != identity[k] for k in ("owner_id", "lease_id")):
            await reply("rejected", code="id_conflict")
            return
        if record and "result" in record:
            await reply(**record["result"])
            return
        if not self._owner_valid(p, "enrollment"):
            await reply("rejected", code="owner_required")
            return
        kind = p["type"]
        enrollment = self.enrollment
        if kind == "enrollment_begin":
            verifier = self.session.speaker_verifier
            if not verifier or not verifier.is_available():
                await reply("rejected", code="model_unavailable")
                return
            try:
                contract = self._contract(verifier)
            except OutdoorViolation:
                await reply("rejected", code="model_provenance")
                return
            if enrollment is None or enrollment["enrollment_id"] != identity["enrollment_id"]:
                if record is None and (len(self.store.enrollments) >= 512 or
                        sum(k[0] == self.device for k in self.store.enrollments) >= 128):
                    await reply("rejected", code="capacity")
                    return
                self._discard_enrollment()
                self.store.enrollments[key] = dict(identity)
                await self._drain()
                self.enrollment = dict(**identity, samples={}, order=[], saved=False,
                    verifier=verifier, extractor=verifier.extract_embedding, contract=contract,
                    generation=self.generation)
            await reply("collecting", required_samples=3, required_holdouts=1)
        elif kind == "enrollment_cancel":
            if record is None:
                if (len(self.store.enrollments) >= 512 or
                        sum(k[0] == self.device for k in self.store.enrollments) >= 128):
                    await reply("rejected", code="capacity")
                    return
                self.store.enrollments[key] = dict(**identity,
                    result=dict(state="cancelled"), terminal_at=self.store.clock())
            if enrollment and enrollment["enrollment_id"] == identity["enrollment_id"]:
                self._discard_enrollment()
                await self._drain()
            await reply("cancelled")
        elif kind == "enrollment_save":
            if not enrollment or enrollment["enrollment_id"] != identity["enrollment_id"]:
                await reply("rejected", code="enrollment_required")
                return
            if enrollment["saved"]:
                await reply("saved")
                return
            ids, holdout = p.get("sample_utterance_ids"), p.get("holdout_utterance_id")
            if type(ids) is not list or len(ids) != 3:
                await reply("rejected", code="invalid_sample_count")
                return
            for value in [*ids, holdout]:
                self._uuid(value)
            if len(set([*ids, holdout])) != 4 or enrollment["order"] != [*ids, holdout]:
                await reply("rejected", code="invalid_sample_ids")
                return
            if not self._enrollment_current(enrollment):
                await reply("rejected", code="model_provenance")
                return
            samples = enrollment["samples"]
            if any(value not in samples for value in [*ids, holdout]):
                await reply("rejected", code="invalid_sample_ids")
                return
            centroid, code = prepare_profile([samples[value] for value in ids], samples[holdout],
                threshold=max(DEFAULT_THRESHOLD, getattr(enrollment["verifier"], "threshold", DEFAULT_THRESHOLD)))
            if centroid is None:
                await reply("rejected", code=code)
                return
            # No await between the final lease/provenance fence and atomic publication.
            if not self._enrollment_current(enrollment):
                await reply("rejected", code="owner_required")
                return
            ok, _ = enrollment["verifier"].save_embedding(
                centroid, min(samples[value][1] for value in ids), "owner")
            enrollment["saved"] = bool(ok)
            result = dict(state="saved" if ok else "rejected",
                          code="ok" if ok else "publication_failed")
            self.store.enrollments[key].update(result=result, terminal_at=self.store.clock())
            self._discard_enrollment()
            await reply(**result)
        else:
            raise OutdoorViolation("invalid_message")

    def _enrollment_current(self, enrollment):
        if (self.closed or self.enrollment is not enrollment
            or not self._owner_valid(enrollment, "enrollment")
            or self.generation != enrollment["generation"]
            or self.session.speaker_verifier is not enrollment["verifier"]
            or self.session.speaker_verifier.extract_embedding != enrollment["extractor"]):
            return False
        try:
            return self._contract(enrollment["verifier"]) == enrollment["contract"]
        except (OutdoorViolation, AttributeError, TypeError, ValueError):
            return False

    def _upload_authorized(self, entry):
        if entry["purpose"] == "conversation":
            return self.output_allowed() and self.owner is None
        enrollment = self.enrollment
        return bool(enrollment and not enrollment["saved"] and self._enrollment_current(enrollment)
                    and entry.get("lease_id") == enrollment["lease_id"]
                    and entry.get("enrollment_id") == enrollment["enrollment_id"]
                    and len(enrollment["order"]) < 4)

    async def _extract_sample(self, entry, pcm, enrollment, deadline):
        if assess_pcm(pcm) is not None:
            return "quality_insufficient"
        if not self._enrollment_current(enrollment):
            return "model_provenance"
        if self.device in self.store.extracting or len(self.store.extracting) >= 4:
            return "processing_failed"
        self.store.extracting.add(self.device)
        worker = self._spawn(asyncio.to_thread(enrollment["extractor"], pcm))
        self._extractions.add(worker)
        def extracted(task):
            self._extractions.discard(task)
            self.store.extracting.discard(self.device)
            self._cleanup_registration()
        worker.add_done_callback(extracted)
        embedding = await asyncio.shield(worker)
        if (self.store.clock() >= deadline or not self._enrollment_current(enrollment)):
            return "model_provenance"
        normalized = normalize_embedding(embedding)
        if normalized is None:
            return "invalid_embedding"
        enrollment["samples"][entry["utterance_id"]] = (normalized, len(pcm))
        enrollment["order"].append(entry["utterance_id"])
        return "ok"

    async def _error(self, request, utterance, code):
        self.store.metrics[code] += 1
        await self._send("utterance_error", request, utterance_id=utterance,
                         code=code, retryable=code in {"capacity", "busy", "upload_timeout"})

    async def _utterance(self, p, request):
        utterance = self._uuid(p.get("utterance_id"))
        kind = p["type"]
        self.store.prune()
        previous = self.store.ledger.get((self.device, utterance))
        if previous is self.assembly and previous is not None:
            if await self._check_upload_deadline(previous):
                return
        if kind == "utterance_status":
            await self._replay(request, utterance, previous)
            return
        if kind == "utterance_cancel":
            if previous is None:
                if not self.store.has_capacity(self.device):
                    await self._error(request, utterance, "capacity")
                    return
                previous = dict(utterance_id=utterance)
                self._terminal(previous, "cancelled")
                self.store.ledger[(self.device, utterance)] = previous
            if self.enrollment and not self.enrollment["saved"]:
                self.enrollment["samples"].pop(utterance, None)
                if utterance in self.enrollment["order"]:
                    self.enrollment["order"].remove(utterance)
            if previous and previous.get("stage") in ("receiving", "accepted"):
                await self._drain()
                self._terminal(previous, "cancelled")
                self.store.ledger[(self.device, utterance)] = previous
            await self._send("utterance_ack", request, utterance_id=utterance, stage="cancelled")
            return
        if previous and "total_samples" not in previous and kind == "utterance_commit":
            await self._replay(request, utterance, previous, duplicate=True)
            return
        if kind in ("utterance_begin", "utterance_commit") and previous and "total_samples" in previous:
            fields = ("total_samples", "sha256")
            if kind == "utterance_begin":
                fields += ("purpose", "end_reason", "lease_id", "enrollment_id")
            if any(type(p.get(k)) is not type(previous.get(k)) or p.get(k) != previous.get(k) for k in fields):
                await self._error(request, utterance, "id_conflict")
                return
            if previous["stage"] != "receiving":
                await self._replay(request, utterance, previous, duplicate=True)
                return
            if kind == "utterance_begin":
                await self._send("utterance_ack", request, utterance_id=utterance,
                                 stage="receiving", next_sample=len(previous["pcm"])//2)
                return
        if kind == "utterance_begin":
            if (type(p.get("total_samples")) is not int or not 1 <= p["total_samples"] <= self.MAX_SAMPLES
                or type(p.get("sha256")) is not str or len(p["sha256"]) != 64
                or any(c not in "0123456789abcdef" for c in p["sha256"])
                or p.get("purpose") not in ("conversation", "enrollment")
                or p.get("end_reason") not in ("manual_finish", "vad_end", "duration_limit")):
                await self._error(request, utterance, "invalid_message")
                return
            if p["purpose"] == "enrollment":
                self._uuid(p.get("lease_id"))
                self._uuid(p.get("enrollment_id"))
            if previous and "total_samples" not in previous:
                previous.update({k: p.get(k) for k in ("total_samples", "sha256", "purpose",
                                                       "end_reason", "lease_id", "enrollment_id")})
                await self._replay(request, utterance, previous, duplicate=True)
                return
            if not self._upload_authorized(p):
                await self._error(request, utterance, "owner_required")
                return
            if self.busy():
                await self._error(request, utterance, "busy")
                return
            if not self.store.has_capacity(self.device):
                await self._error(request, utterance, "capacity")
                return
            entry = dict(utterance_id=utterance, purpose=p["purpose"],
                         total_samples=p["total_samples"], sha256=p["sha256"],
                         end_reason=p["end_reason"], pcm=bytearray(), request=request,
                         stage="receiving", lease_id=p.get("lease_id"),
                         enrollment_id=p.get("enrollment_id"))
            self.assembly = entry
            self.store.ledger[(self.device, utterance)] = entry
            now = self.store.clock()
            entry.update(wall_deadline=now + self.UPLOAD_TIMEOUT,
                         progress_deadline=now + self.INACTIVITY_TIMEOUT)
            self._arm_upload(entry)
            await self._send("utterance_ack", request, utterance_id=utterance,
                             stage="begin", next_sample=0)
        elif kind == "utterance_commit":
            entry = self.assembly
            if entry is None or entry["utterance_id"] != utterance:
                await self._error(request, utterance, "invalid_message")
                return
            if any(type(p.get(k)) is not type(entry[k]) or p.get(k) != entry[k]
                   for k in ("total_samples", "sha256")):
                await self._error(request, utterance, "id_conflict")
                return
            if not self._upload_authorized(entry):
                self._discard_assembly()
                await self._error(request, utterance, "owner_required")
                return
            pcm = bytes(entry["pcm"])
            self._discard_assembly()
            entry.pop("pcm", None)
            if len(pcm) != entry["total_samples"] * 2:
                await self._error(request, utterance, "invalid_length")
                return
            if hashlib.sha256(pcm).hexdigest() != entry["sha256"]:
                await self._error(request, utterance, "checksum_mismatch")
                return
            self.assembly = None
            entry.update(stage="accepted", request=request,
                         deadline=self.store.clock() + self.PROCESSING_TIMEOUT)
            self.store.ledger[(self.device, utterance)] = entry
            self._active_entry = entry
            generation = self.generation
            try:
                await self._send("utterance_ack", request, utterance_id=utterance,
                                 stage="accepted", total_samples=entry["total_samples"],
                                 sha256=entry["sha256"], duplicate=False)
            except BaseException:
                self._terminal(entry, "cancelled")
                raise
            if self.closed or self.generation != generation or entry["stage"] != "accepted":
                self._terminal(entry, "cancelled")
                return
            self.active_task = self._spawn(self._process(entry, pcm, generation))
        else:
            raise OutdoorViolation("invalid_message")

    async def binary(self, data):
        async with self._control_lock:
            await self._binary(data)

    async def _binary(self, data):
        if type(data) is not bytes or len(data) < 28 or self.closed:
            raise OutdoorViolation("invalid_format")
        magic, raw_id, offset, count = struct.unpack(">4s16sII", data[:28])
        entry = self.assembly
        if magic != b"NXA2" or entry is None or str(uuid.UUID(bytes=raw_id)) != entry["utterance_id"]:
            raise OutdoorViolation("invalid_format")
        if await self._check_upload_deadline(entry):
            return
        if count != len(data) - 28:
            raise OutdoorViolation("invalid_format")
        if not 2 <= count <= self.MAX_CHUNK_BYTES or count % 2 or offset * 2 + count > entry["total_samples"] * 2:
            raise OutdoorViolation("invalid_length")
        start = offset * 2
        received = len(entry["pcm"])
        if start > received:
            raise OutdoorViolation("sequence_gap")
        if start < received:
            if start + count > received or entry["pcm"][start:start+count] != data[28:]:
                raise OutdoorViolation("overlap_conflict")
        else:
            entry["pcm"].extend(data[28:])
            entry["progress_deadline"] = self.store.clock() + self.INACTIVITY_TIMEOUT
            self._arm_upload(entry)
        await self._send("utterance_ack", entry["request"],
                         utterance_id=entry["utterance_id"], stage="receiving",
                         next_sample=len(entry["pcm"])//2)

    async def _replay(self, request, utterance, entry, duplicate=False):
        fields = dict(utterance_id=utterance, stage=("rejected" if entry["stage"] == "failed" else entry["stage"]) if entry else "unknown")
        if entry:
            if entry["stage"] == "receiving":
                fields["next_sample"] = len(entry["pcm"])//2
            else:
                fields["duplicate"] = duplicate
                if "total_samples" in entry:
                    fields.update(total_samples=entry["total_samples"], sha256=entry["sha256"])
        await self._send("utterance_ack", request, **fields)

    async def _conversation(self, pcm):
        runner = getattr(self.session, "_run_outdoor_pipeline", self.session._run_pipeline)
        code = await runner(pcm)
        if self.is_current_output():
            await self.session._resume_listening()
        return code

    async def _process(self, entry, pcm, generation):
        token = self._output_generation.set(generation)
        identity_token = self.output_utterance.set(
            entry["utterance_id"] if entry["purpose"] == "conversation" else None)
        try:
            remaining = entry["deadline"] - self.store.clock()
            if remaining <= 0:
                self._terminal(entry, "failed")
                self.paused = True
                self.store.metrics["processing_timeout"] += 1
                await self._send("utterance_result", entry["request"],
                    utterance_id=entry["utterance_id"], status="failed", code="processing_timeout")
                return
            if entry["purpose"] == "enrollment":
                self._worker = self._spawn(self._extract_sample(entry, pcm, self.enrollment,
                    entry["deadline"]))
            else:
                self._worker = self._spawn(self._conversation(pcm))
            done, _ = await asyncio.wait({self._worker}, timeout=remaining)
            if not done:
                self.generation += 1
                self.paused = True
                self._worker.cancel()
                stage, code = "failed", "processing_timeout"
            else:
                code = self._worker.result() or "ok"
                if code == "identity_mismatch":
                    code = "speaker_rejected"
                if type(code) is not str or code not in {"ok", "no_speech", "quality_insufficient", "invalid_embedding",
                    "model_provenance", "stt_empty", "speaker_rejected", "processing_failed",
                    "stt_failed", "hermes_failed", "tts_failed", "cancelled"}:
                    code = "processing_failed"
                stage = "completed" if code == "ok" else "failed"
            if not self.closed and entry["stage"] == "accepted":
                self._terminal(entry, stage)
                self.store.metrics[code] += 1
                await self._send("utterance_result", entry["request"],
                    utterance_id=entry["utterance_id"], status=stage, code=code)
        except asyncio.CancelledError:
            if entry["stage"] == "accepted":
                self._terminal(entry, "cancelled")
        except Exception:
            if entry["stage"] == "accepted":
                self._terminal(entry, "failed")
                self.store.metrics["processing_failed"] += 1
                if not self.closed:
                    await self._send("utterance_result", entry["request"],
                        utterance_id=entry["utterance_id"], status="failed", code="processing_failed")
        finally:
            self.output_utterance.reset(identity_token)
            self._output_generation.reset(token)

    async def close(self):
        self.closed = True
        self.paused = True
        self._retire_owner()
        self._discard_enrollment()
        if self._lease_handle:
            self._lease_handle.cancel()
        try:
            await self._drain()
        finally:
            self._cleanup_registration()
