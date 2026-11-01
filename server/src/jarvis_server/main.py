"""Main entrypoint for Jarvis Appliance Server."""

from contextlib import asynccontextmanager
import asyncio
import logging
import uuid
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
import uvicorn

from .config import load_config
from .auth import DeviceRegistry
from .gateway.websocket_handler import ApplianceSession, ProtocolViolation
from .pipeline.stt import create_stt_engine
from .pipeline.tts import create_tts_engine
from .hermes.agent_bridge import create_hermes_bridge

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
    yield
    logger.info("Shutting down Jarvis Server...")


app = FastAPI(
    title="Jarvis Appliance Gateway",
    version="0.1.0",
    description="Dedicated Ambient AI Appliance Gateway for YH Dev",
    lifespan=lifespan,
)


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
