"""Main entrypoint for Jarvis Appliance Server."""

from contextlib import asynccontextmanager
import logging
import uuid
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
import uvicorn

from .config import load_config
from .gateway.websocket_handler import ApplianceSession
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


@app.websocket("/ws/appliance")
async def websocket_appliance_endpoint(websocket: WebSocket):
    await websocket.accept()
    session_id = f"sess_{uuid.uuid4().hex[:8]}"
    logger.info(f"New client connected: {session_id} from {websocket.client}")

    session = ApplianceSession(
        websocket=websocket,
        session_id=session_id,
        stt=stt_engine,
        tts=tts_engine,
        hermes=hermes_bridge,
    )

    try:
        while True:
            message = await websocket.receive()
            if "text" in message and message["text"]:
                await session.handle_text_frame(message["text"])
            elif "bytes" in message and message["bytes"]:
                await session.handle_binary_frame(message["bytes"])
    except (WebSocketDisconnect, RuntimeError):
        logger.info(f"Client disconnected: {session_id}")
    except Exception as e:
        logger.error(f"Error in session {session_id}: {e}", exc_info=True)
    finally:
        logger.info(f"Cleaned up session {session_id}")


def run():
    uvicorn.run(
        app,
        host=config.server.host,
        port=config.server.port,
        log_level="info",
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    run()
