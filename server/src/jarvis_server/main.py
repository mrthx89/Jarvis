"""Main entrypoint for Jarvis Appliance Server."""

from contextlib import asynccontextmanager
import asyncio
import json
import logging
import time
from typing import Optional
import uuid
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
import uvicorn

from .config import load_config
from .auth import DeviceRegistry
from .gateway.websocket_handler import ApplianceSession, ProtocolViolation
from .pipeline.stt import create_stt_engine
from .pipeline.tts import create_tts_engine
from .hermes.agent_bridge import create_hermes_bridge
from .session_hub import hub

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("jarvis_server")

config = load_config()
stt_engine = create_stt_engine(provider=config.stt.provider, model=config.stt.model)
tts_engine = create_tts_engine(provider=config.tts.provider, voice=config.tts.voice)
hermes_bridge = create_hermes_bridge(
    mode=config.hermes.bridge_mode, hermes_binary=config.hermes.hermes_binary
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing Jarvis Dedicated Appliance Server...")
    logger.info(f"STT Provider: {config.stt.provider} | TTS Provider: {config.tts.provider}")
    logger.info(f"Hermes Mode: {config.hermes.bridge_mode}")
    hub.set_hermes_bridge(hermes_bridge)
    hub.set_tts_engine(tts_engine)
    hub.set_stt_engine(stt_engine)
    hub.set_session_thread(getattr(config.hermes, "session_name", "appliance"))
    yield
    logger.info("Shutting down Jarvis Server...")


app = FastAPI(
    title="Jarvis Appliance Gateway",
    version="0.1.0",
    description="Dedicated Ambient AI Appliance Gateway for YH Dev",
    lifespan=lifespan,
)


class SessionSendPayload(BaseModel):
    message: str
    speak: bool = False
    session_name: Optional[str] = None


class SessionSpeakPayload(BaseModel):
    text: str
    expression: Optional[str] = None


class SessionLinkPayload(BaseModel):
    session_name: str


@app.get("/health")
async def health_check():
    """Health check endpoint for monitor/kiosk connectivity verification."""
    return JSONResponse(
        {
            "status": "healthy",
            "appliance": "Jarvis Dedicated Appliance",
            "version": "0.1.0",
            "agent": "Jarvis",
            "ethos": "berkarya dengan hati @ 2026",
        }
    )


@app.post("/session/send")
async def session_send_endpoint(payload: SessionSendPayload):
    """Send text instruction to session, optionally triggering audio output on appliance."""
    res = await hub.inject_message(
        payload.message,
        speak=payload.speak,
        session_name=payload.session_name,
    )
    return JSONResponse(res)


@app.post("/session/speak")
async def session_speak_endpoint(payload: SessionSpeakPayload):
    """Trigger audio response / speech on connected appliance screen/speaker."""
    res = await hub.trigger_speech(payload.text, expression=payload.expression)
    return JSONResponse(res)


@app.post("/session/link")
async def session_link_endpoint(payload: SessionLinkPayload):
    """Link Hermes session thread with appliance."""
    name = hub.set_session_thread(payload.session_name)
    return JSONResponse({"status": "ok", "session_name": name})


@app.get("/session/status")
async def session_status_endpoint():
    """Return status of appliance connectivity and session hub."""
    return JSONResponse(hub.get_status())


@app.get("/session/transcript")
async def session_transcript_endpoint(limit: int = 50):
    """Retrieve recent transcript entries."""
    return JSONResponse({
        "session_name": hub.current_session_thread,
        "transcript": hub.get_transcript(limit=limit),
    })


@app.get("/session/stream")
async def session_stream_endpoint():
    """Server-Sent Events stream for cognitive events, state changes, and transcripts."""
    q = hub.subscribe()

    async def event_generator():
        try:
            yield f"data: {json.dumps({'type': 'status', 'status': hub.get_status()})}\n\n"
            while True:
                event = await q.get()
                yield f"data: {json.dumps(event)}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            hub.unsubscribe(q)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.websocket("/ws/session/stream")
async def websocket_session_stream_endpoint(websocket: WebSocket):
    """Full-duplex WebSocket stream for terminal sessions to interact and listen."""
    await websocket.accept()
    q = hub.subscribe()
    await websocket.send_json({"type": "status", "status": hub.get_status()})

    async def push_events():
        try:
            while True:
                event = await q.get()
                await websocket.send_json(event)
        except (asyncio.CancelledError, WebSocketDisconnect):
            pass
        except Exception:
            pass

    push_task = asyncio.create_task(push_events())
    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")
            if msg_type == "send":
                res = await hub.inject_message(
                    data.get("message", ""),
                    speak=data.get("speak", False),
                    session_name=data.get("session_name"),
                )
                await websocket.send_json({"type": "send_response", **res})
            elif msg_type == "speak":
                res = await hub.trigger_speech(
                    data.get("text", ""),
                    expression=data.get("expression"),
                )
                await websocket.send_json({"type": "speak_response", **res})
            elif msg_type == "link":
                res = hub.set_session_thread(data.get("session_name", "appliance"))
                await websocket.send_json({"type": "link_response", "session_name": res})
            elif msg_type == "status":
                await websocket.send_json({"type": "status", "status": hub.get_status()})
            elif msg_type == "ping":
                await websocket.send_json({
                    "type": "pong",
                    "timestamp": data.get("timestamp", int(time.time() * 1000)),
                })
    except WebSocketDisconnect:
        pass
    finally:
        push_task.cancel()
        hub.unsubscribe(q)


# Process-local admission: run one worker or divide capacity across workers.
_active_sessions = 0


async def _deny(websocket, status, code):
    denial = getattr(websocket, "send_denial_response", None)
    if callable(denial) and "websocket.http.response" in websocket.scope.get("extensions", {}):
        await denial(JSONResponse({"detail": "Device authorization required"}, status_code=status))
    else:
        # ASGI servers without denial extension return HTTP 403 before accept.
        await websocket.close(code=code)


@app.websocket("/ws/appliance")
async def websocket_appliance_endpoint(websocket: WebSocket):
    global _active_sessions
    device_id = websocket.headers.get("x-device-id", "")
    registry = DeviceRegistry(config.server.device_registry_path)
    if not registry.authenticate(device_id, websocket.headers.get("authorization", "")):
        await _deny(websocket, 403, 1008)
        return
    if _active_sessions >= config.server.max_sessions:
        await _deny(websocket, 503, 1013)
        return
    # No await between admission check and reservation.
    _active_sessions += 1
    session_id = f"sess_{uuid.uuid4().hex[:8]}"
    session = ApplianceSession(
        websocket=websocket, session_id=session_id,
        stt=stt_engine, tts=tts_engine, hermes=hermes_bridge,
        authenticated_device_id=device_id, max_json_bytes=config.server.max_json_bytes,
        session_hub=hub,
    )
    try:
        await websocket.accept()
        logger.info("Device connected: %s", session_id)
        hello_deadline = asyncio.get_running_loop().time() + config.server.hello_timeout_seconds
        while True:
            if session.ready:
                message = await websocket.receive()
            else:
                remaining = hello_deadline - asyncio.get_running_loop().time()
                message = await asyncio.wait_for(websocket.receive(), max(0, remaining))
            if message.get("type") == "websocket.disconnect":
                break
            # Reload on every incoming frame: revocation applies before any new work.
            if not registry.authenticate(device_id, websocket.headers.get("authorization", "")):
                await websocket.close(code=1008, reason="Device authorization required")
                break
            if message.get("text") is not None:
                await session.handle_text_frame(message["text"])
            elif message.get("bytes") is not None:
                await session.handle_binary_frame(message["bytes"])
    except (ProtocolViolation, asyncio.TimeoutError):
        await websocket.close(code=1008, reason="Protocol setup required")
    except WebSocketDisconnect:
        logger.info("Device disconnected: %s", session_id)
    except Exception:
        # Exception strings/tracebacks can include provider payloads. Do not log them.
        logger.error("Session failure: %s", session_id)
        await websocket.close(code=1011, reason="Session failure")
    finally:
        try:
            await session.close()
        finally:
            _active_sessions -= 1
        logger.info("Cleaned up session %s", session_id)


def run():
    uvicorn.run(
        app,
        host=config.server.host,
        port=config.server.port,
        log_level="info",
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
        ws_max_size=max(config.server.max_json_bytes, ApplianceSession.MAX_FRAME_BYTES),
        ws_max_queue=4,
    )


if __name__ == "__main__":
    run()
