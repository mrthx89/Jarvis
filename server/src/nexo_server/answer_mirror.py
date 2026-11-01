"""Server-owned final-answer forwarding for one pinned Hermes conversation.

Reads persisted finals only; never invokes the model or writes Hermes history.
Pending speech is bounded, expires, and is discarded while the device is off.
"""
import asyncio
from collections import deque
import logging
import time

from .mascot import contextual_expression

logger = logging.getLogger(__name__)


class AnswerMirror:
    def __init__(self, reader, hub, bridge):
        self.reader = reader
        self.hub = hub
        self.bridge = bridge
        self.pending = deque(maxlen=20)
        self._read_lock = asyncio.Lock()
        self._deferred = []
        self._session_floor = 0
        self.hub.add_session_listener(self._invalidate_session)

    def _invalidate_session(self):
        self.pending.clear()
        self._deferred.clear()
        # Snapshot only: the reader thread alone advances its cursor. Filtering
        # by this boundary also invalidates reads already running in to_thread.
        self._session_floor = max(self._session_floor, self.reader.high_water())

    async def _collect(self, suppress=None, through=None):
        async with self._read_lock:
            rows = self._deferred + await asyncio.to_thread(self.reader.read_new)
            self._deferred = []
            rows = [row for row in rows if row["id"] > self._session_floor]
            if suppress is None and self.bridge.query_active:
                self._deferred = rows
                return
            # Persistence is complete before on_reply. Identify its exact ID
            # across all unread batches, then consume only through that snapshot.
            # Keep the existing bounded speech queue, never accumulate a backlog
            # of reader batches or skip terminal finals to advance the cursor.
            owned_id = None
            if through is not None:
                after = rows[0]["id"] - 1 if rows else self.reader.cursor
                owned_id = await asyncio.to_thread(
                    self.reader.last_matching_final, suppress, after, through)
            while True:
                for row in rows:
                    if row["id"] > self._session_floor and row["id"] != owned_id:
                        self.pending.append({**row, "queued_at": time.monotonic()})
                if through is None or self.reader.cursor >= through:
                    break
                previous = self.reader.cursor
                rows = await asyncio.to_thread(self.reader.read_new)
                if self.reader.cursor <= previous:
                    break

    async def on_bridge_reply(self, reply):
        """Voice/server-send already owns its reply's subtitle and speech."""
        # Capture before yielding: a later identical terminal answer is not owned.
        await self._collect(suppress=reply, through=self.reader.high_water())

    async def poll_once(self):
        if not self.bridge.query_active:
            await self._collect()

    async def deliver_once(self):
        device = self.hub.get_active_appliance()
        if device is None:
            # STOP disconnects the device: never replay old replies after START.
            self.pending.clear()
            return
        while self.pending and time.monotonic() - self.pending[0]["queued_at"] > 120:
            self.pending.popleft()
        if not self.pending:
            return
        row = self.pending[0]
        expression = contextual_expression("", row["text"]).value
        # speak_text atomically reserves audio and sends the full final subtitle.
        if await device.speak_text(row["text"], expression=expression):
            # A disconnect may have invalidated this row while speech yielded.
            if not self.pending or self.pending[0] is not row:
                return
            self.pending.popleft()
            self.hub.publish_event({
                "type": "transcription", "role": "assistant",
                "text": row["text"], "expression": expression,
                "source": "terminal_mirror", "message_id": row["id"],
            })

    async def run(self):
        while True:
            try:
                await self.poll_once()
                await self.deliver_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Do not log transcripts or provider exception payloads.
                logger.warning("Nexo answer mirroring temporarily unavailable")
            await asyncio.sleep(0.25)
