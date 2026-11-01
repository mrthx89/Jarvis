package com.yhdev.jarvis.network

import com.google.gson.annotations.SerializedName

data class AudioSpec(
    @SerializedName("format") val format: String = "audio/pcm",
    @SerializedName("sample_rate") val sampleRate: Int = 16000,
    @SerializedName("channels") val channels: Int = 1,
    @SerializedName("bit_depth") val bitDepth: Int = 16
)

data class ClientHello(
    @SerializedName("type") val type: String = "client_hello",
    @SerializedName("protocol_version") val protocolVersion: String = "1.0",
    @SerializedName("device_id") val deviceId: String,
    @SerializedName("audio_spec") val audioSpec: AudioSpec = AudioSpec()
)

data class ServerHello(
    @SerializedName("type") val type: String,
    @SerializedName("session_id") val sessionId: String,
    @SerializedName("agent_name") val agentName: String,
    @SerializedName("status") val status: String
)

data class VoiceStart(
    @SerializedName("type") val type: String = "voice_start",
    @SerializedName("timestamp") val timestamp: Long = System.currentTimeMillis()
)

data class VoiceEnd(
    @SerializedName("type") val type: String = "voice_end",
    @SerializedName("timestamp") val timestamp: Long = System.currentTimeMillis()
)

data class StateChange(
    @SerializedName("type") val type: String,
    @SerializedName("state") val state: String,
    @SerializedName("message") val message: String? = null,
    @SerializedName("expression") val expression: String? = null
)

data class VisualTelemetry(
    @SerializedName("type") val type: String,
    @SerializedName("amplitude") val amplitude: Float,
    @SerializedName("frequency_bands") val frequencyBands: List<Float>? = null
)

data class TranscriptionMessage(
    @SerializedName("type") val type: String,
    @SerializedName("role") val role: String,
    @SerializedName("text") val text: String,
    @SerializedName("is_final") val isFinal: Boolean = true
)

data class PingMessage(
    @SerializedName("type") val type: String = "ping",
    @SerializedName("timestamp") val timestamp: Long = System.currentTimeMillis()
)

data class PongMessage(
    @SerializedName("type") val type: String,
    @SerializedName("timestamp") val timestamp: Long
)
