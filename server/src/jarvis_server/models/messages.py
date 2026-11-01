"""Protocol messages according to docs/PROTOCOL.md."""

from enum import Enum
from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class JarvisState(str, Enum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    SPEAKING = "SPEAKING"
    ERROR = "ERROR"
    DISCONNECTED = "DISCONNECTED"


# Dual-compatibility alias
NexoState = JarvisState


class AudioSpec(BaseModel):
    format: str = "audio/pcm"
    sample_rate: int = 16000
    channels: int = 1
    bit_depth: int = 16


class ClientHello(BaseModel):
    type: Literal["client_hello"] = "client_hello"
    protocol_version: str = "1.0"
    device_id: str
    audio_spec: AudioSpec = Field(default_factory=AudioSpec)


class ServerHello(BaseModel):
    type: Literal["server_hello"] = "server_hello"
    session_id: str
    agent_name: str = "Jarvis"
    status: JarvisState = JarvisState.IDLE


class VoiceStart(BaseModel):
    type: Literal["voice_start"] = "voice_start"
    timestamp: int


class VoiceEnd(BaseModel):
    type: Literal["voice_end"] = "voice_end"
    timestamp: int


class StateChange(BaseModel):
    type: Literal["state_change"] = "state_change"
    state: JarvisState
    message: Optional[str] = None
    expression: Optional[str] = None


class VisualTelemetry(BaseModel):
    type: Literal["visual_telemetry"] = "visual_telemetry"
    amplitude: float = Field(ge=0.0, le=1.0)
    frequency_bands: List[float] = Field(default_factory=list)


class TranscriptionMessage(BaseModel):
    type: Literal["transcription"] = "transcription"
    role: Literal["user", "assistant"]
    text: str
    is_final: bool = True


class TTSStart(BaseModel):
    type: Literal["tts_start"] = "tts_start"
    format: str = "audio/pcm"
    sample_rate: int = 24000
    channels: int = 1


class TTSEnd(BaseModel):
    type: Literal["tts_end"] = "tts_end"


class PingMessage(BaseModel):
    type: Literal["ping"] = "ping"
    timestamp: int


class PongMessage(BaseModel):
    type: Literal["pong"] = "pong"
    timestamp: int
