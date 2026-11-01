"""Session hub and cognitive stream dispatcher for Nexo Appliance."""

import asyncio
from datetime import datetime, timezone
import logging
import time
from typing import Any, Dict, List, Optional, Set

from .models.messages import NexoState
from .mascot import (
    contextual_expression,
    parse_expression_command,
    parse_and_strip_emotional_tags,
    strip_emotional_tags,
)

logger = logging.getLogger(__name__)


class SessionHub:
    """Coordinates active appliance sessions, events, and terminal bridges."""

    def __init__(self):
        self._active_sessions: Dict[str, Any] = {}
        self._subscribers: Set[asyncio.Queue] = set()
        self._event_history: List[Dict[str, Any]] = []
        self._max_history: int = 200
        self._hermes_bridge: Optional[Any] = None
        self._tts_engine: Optional[Any] = None
        self._stt_engine: Optional[Any] = None
        self._current_session_thread: str = "nexo"
        self._lock = asyncio.Lock()
        self._session_listeners = set()

    def add_session_listener(self, listener) -> None:
        """Observe synchronous connection boundaries, including between ticks."""
        self._session_listeners.add(listener)

    def _notify_session_change(self) -> None:
        for listener in self._session_listeners:
            listener()

    def set_hermes_bridge(self, bridge: Any) -> None:
        self._hermes_bridge = bridge

    def set_tts_engine(self, tts: Any) -> None:
        self._tts_engine = tts

    def set_stt_engine(self, stt: Any) -> None:
        self._stt_engine = stt

    @property
    def current_session_thread(self) -> str:
        return self._current_session_thread

    def set_session_thread(self, name: str) -> str:
        if not isinstance(name, str) or name.strip() != "nexo":
            raise ValueError("Session routing is locked to nexo")
        self.publish_event({
            "type": "session_linked",
            "session_name": "nexo",
            "timestamp": int(time.time() * 1000),
        })
        return self._current_session_thread

    def register_session(self, session: Any) -> None:
        self._active_sessions[session.session_id] = session
        self._notify_session_change()
        self.publish_event({
            "type": "appliance_connected",
            "session_id": session.session_id,
            "device_id": getattr(session, "authenticated_device_id", None),
            "timestamp": int(time.time() * 1000),
        })
        logger.info("SessionHub: registered session %s", session.session_id)

    def unregister_session(self, session: Any) -> None:
        if session.session_id in self._active_sessions:
            del self._active_sessions[session.session_id]
            self._notify_session_change()
            self.publish_event({
                "type": "appliance_disconnected",
                "session_id": session.session_id,
                "timestamp": int(time.time() * 1000),
            })
            logger.info("SessionHub: unregistered session %s", session.session_id)

    def get_active_appliance(self) -> Optional[Any]:
        for s in self._active_sessions.values():
            if getattr(s, "ready", False) and not getattr(s, "_closed", False):
                return s
        return None

    def publish_event(self, event: Dict[str, Any]) -> None:
        if "timestamp" not in event:
            event["timestamp"] = int(time.time() * 1000)
        if "iso_time" not in event:
            event["iso_time"] = datetime.now(timezone.utc).isoformat()

        self._event_history.append(event)
        if len(self._event_history) > self._max_history:
            self._event_history.pop(0)

        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def _get_hermes_session_id(self) -> Optional[str]:
        getter = getattr(self._hermes_bridge, "get_session_id", None)
        session_id = getter() if callable(getter) else None
        return session_id if isinstance(session_id, str) and session_id else None

    def get_status(self) -> Dict[str, Any]:
        appliance = self.get_active_appliance()
        state_raw = getattr(appliance, "state", "IDLE") if appliance else "IDLE"
        state_val = getattr(state_raw, "value", state_raw)
        return {
            "appliance_connected": appliance is not None,
            "active_sessions_count": len(self._active_sessions),
            "session_thread": self._current_session_thread,
            "session_locked": True,
            "hermes_session_id": self._get_hermes_session_id(),
            "device_id": getattr(appliance, "authenticated_device_id", None) if appliance else None,
            "state": str(state_val),
            "hermes_mode": getattr(self._hermes_bridge, "mode", "cli") if self._hermes_bridge else "unknown",
            "active_subscribers": len(self._subscribers),
        }

    def get_transcript(self, limit: int = 50) -> List[Dict[str, Any]]:
        transcripts = [e for e in self._event_history if e.get("type") == "transcription"]
        return transcripts[-limit:]

    def clear_history(self) -> None:
        self._event_history.clear()

    async def inject_message(
        self,
        message: str,
        speak: bool = False,
        session_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        target_session = "nexo" if session_name is None else session_name
        if not isinstance(target_session, str):
            raise ValueError("Session routing is locked to nexo")
        target_session = target_session.strip()
        bound_id = self._get_hermes_session_id()
        if target_session != "nexo" and (bound_id is None or target_session != bound_id):
            raise ValueError("Session routing is locked to nexo")

        # 1. Publish user transcription event
        self.publish_event({
            "type": "transcription",
            "role": "user",
            "text": message,
            "source": "terminal",
        })

        appliance = self.get_active_appliance()
        if appliance:
            await appliance.send_state(NexoState.THINKING, expression="bingung")

        # 2. Check for local mascot expression command
        cmd_expression = parse_expression_command(message)
        if cmd_expression is not None:
            reply = "Baik, ekspresi wajah sudah diubah."
            expr = cmd_expression.value
        else:
            self.publish_event({
                "type": "cognitive",
                "stage": "thinking",
                "content": f"Querying Hermes in session '{target_session}'...",
            })
            reply = ""
            expr = "netral"
            if self._hermes_bridge:
                import inspect
                used_stream = False
                stream_fn = getattr(self._hermes_bridge, "query_stream", None)
                if stream_fn and inspect.isasyncgenfunction(stream_fn):
                    try:
                        sig = inspect.signature(stream_fn)
                        if "session_name" in sig.parameters:
                            gen = stream_fn(message, session_name=target_session)
                        else:
                            gen = stream_fn(message)

                        collected = []
                        speech_expr = None
                        async for sentence in gen:
                            clean_sentence = sentence.strip()
                            if not clean_sentence:
                                continue
                            tag_expr, clean_sentence = parse_and_strip_emotional_tags(clean_sentence)
                            if not clean_sentence:
                                continue
                            collected.append(clean_sentence)
                            if tag_expr:
                                speech_expr = tag_expr.value
                            elif speech_expr is None:
                                speech_expr = contextual_expression(message, clean_sentence).value

                            self.publish_event({
                                "type": "cognitive",
                                "stage": "streaming",
                                "sentence": clean_sentence,
                                "content": clean_sentence,
                                "index": len(collected) - 1,
                                "expression": speech_expr,
                            })

                        raw_reply = " ".join(collected).strip()
                        tag_expr, reply = parse_and_strip_emotional_tags(raw_reply)
                        if tag_expr:
                            speech_expr = tag_expr.value
                        expr = speech_expr or contextual_expression(message, reply).value
                        used_stream = True
                    except Exception as e:
                        logger.warning("query_stream failed in inject_message, falling back to query: %s", e)
                        used_stream = False

                if not used_stream:
                    sig = inspect.signature(self._hermes_bridge.query)
                    if "session_name" in sig.parameters:
                        raw_reply = await self._hermes_bridge.query(message, session_name=target_session)
                    else:
                        raw_reply = await self._hermes_bridge.query(message)
                    tag_expr, reply = parse_and_strip_emotional_tags(raw_reply)
                    expr = tag_expr.value if tag_expr else contextual_expression(message, reply).value
            else:
                reply = "Hermes bridge belum diinisialisasi."
                expr = "netral"

        # 3. Publish assistant transcription event
        self.publish_event({
            "type": "transcription",
            "role": "assistant",
            "text": reply,
            "expression": expr,
            "source": "nexo_hermes",
        })

        # 4. Trigger audio output if requested
        device_spoken = False
        if speak:
            if appliance:
                device_spoken = await appliance.speak_text(reply, expression=expr)
            elif self._tts_engine:
                # Local synthesis without connected appliance
                device_spoken = False
        else:
            if appliance:
                await appliance.send_state(NexoState.IDLE, expression=expr or "netral")

        return {
            "status": "ok",
            "reply": reply,
            "spoken": speak,
            "device_spoken": device_spoken,
            "session_name": target_session,
            "expression": expr,
        }

    async def trigger_speech(self, text: str, expression: Optional[str] = None) -> Dict[str, Any]:
        appliance = self.get_active_appliance()
        if not appliance:
            return {"status": "error", "message": "No appliance currently connected", "spoken": False}

        spoken = await appliance.speak_text(text, expression=expression)
        return {"status": "ok", "spoken": spoken}


# Global singleton instance for nexo_server
hub = SessionHub()
