"""Static analysis guards for Android mascot visual effects, burn-in protection, earcons, and audio queue."""

import os
from pathlib import Path
import wave
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
ANDROID_MAIN = ROOT / "android/app/src/main"
JAVA_UI = ANDROID_MAIN / "java/id/my/yhdev/nexo/ui"
JAVA_AUDIO = ANDROID_MAIN / "java/id/my/yhdev/nexo/audio"
JAVA_SERVICE = ANDROID_MAIN / "java/id/my/yhdev/nexo/service"
RES_DRAWABLE = ANDROID_MAIN / "res/drawable"
RES_RAW = ANDROID_MAIN / "res/raw"


def test_mascot_sleeping_xml_structure():
    """Verify mascot_sleeping.png exists as high-fidelity RGBA asset and sleeping.svg master has curved eyes."""
    sleeping_png = RES_DRAWABLE / "mascot_sleeping.png"
    assert sleeping_png.exists(), "mascot_sleeping.png must exist in res/drawable"

    sleeping_svg = ROOT / "resource/imoticon/svg/sleeping.svg"
    assert sleeping_svg.exists(), "sleeping.svg master must exist in resource/imoticon/svg"

    svg_content = sleeping_svg.read_text()
    # Check for closed sleeping eye curved paths
    has_left_closed_eye = "M92 120" in svg_content or "Q104" in svg_content
    has_right_closed_eye = "M140 120" in svg_content or "Q152" in svg_content
    assert has_left_closed_eye, "Must contain left sleeping closed eye arc"
    assert has_right_closed_eye, "Must contain right sleeping closed eye arc"


def test_mascot_expression_enum_contains_sleeping():
    """Verify Kotlin MascotExpression and server MascotExpression include SLEEPING."""
    from nexo_server.mascot import MascotExpression

    assert "sleeping" in {e.value for e in MascotExpression}

    kt_source = (JAVA_UI / "MascotExpression.kt").read_text()
    assert "SLEEPING" in kt_source
    assert "mascot_sleeping" in kt_source
    assert '"sleeping"' in kt_source
    assert 'normalized == "tidur"' in kt_source


def test_earcon_audio_files_validity():
    """Verify earcon chime audio files exist and are valid PCM WAV files."""
    required_earcons = [
        "chime_listening.wav",
        "chime_thinking.wav",
        "earcon_listening.wav",
        "earcon_thinking.wav",
    ]
    for earcon_name in required_earcons:
        earcon_path = RES_RAW / earcon_name
        assert earcon_path.exists(), f"{earcon_name} must exist in res/raw"

        with wave.open(str(earcon_path), "rb") as wf:
            assert wf.getnchannels() in (1, 2), "Must be mono or stereo WAV"
            assert wf.getsampwidth() == 2, "Must be 16-bit PCM"
            assert wf.getframerate() > 0, "Valid sample rate required"
            assert wf.getnframes() > 0, "Audio file must contain audio frames"


def test_earcon_manager_implementation():
    """Verify EarconManager uses SoundPool with correct audio attributes."""
    manager_path = JAVA_AUDIO / "EarconManager.kt"
    assert manager_path.exists(), "EarconManager.kt must exist"

    source = manager_path.read_text()
    assert "class EarconManager" in source
    assert "SoundPool" in source
    assert "AudioAttributes" in source
    assert "USAGE_ASSISTANCE_SONIFICATION" in source
    assert "CONTENT_TYPE_SONIFICATION" in source
    assert "playListening" in source
    assert "playThinking" in source
    assert "release" in source


def test_audiotrack_bounded_queue_guard():
    """Verify AudioPlayerManager bounds queued bytes to prevent latency buildup."""
    source = (JAVA_AUDIO / "AudioPlayerManager.kt").read_text()
    assert "maxBufferedBytes" in source
    assert "queuedBytes" in source
    assert "writeChunk(data: ByteArray): Boolean" in source
    assert "dropping chunk to bound latency" in source
    assert "isQueueFull" in source
    assert "getQueuedBytes" in source

    service_source = (JAVA_SERVICE / "NexoKioskService.kt").read_text()
    assert "audioPlayer.writeChunk(audioData)" in service_source
    assert "earconManager.playListening()" in service_source
    assert "earconManager.playThinking()" in service_source


def test_visualizer_living_eyes_and_physics_spring():
    """Verify VisualizerView implements living glowing eyes, spring interpolator, and saccades."""
    source = (JAVA_UI / "VisualizerView.kt").read_text()

    # Living digital glowing eyes with radial gradient
    assert "drawLivingEyeGlow" in source
    assert "eyeGlowPaint" in source
    assert "RadialGradient" in source
    assert "coreColor" in source and "glowColor" in source

    # Organic blinking kinetics
    assert "isBlinking" in source
    assert "blinkSquashY" in source
    assert "blinkStartTime" in source

    # Physiological micro-saccades & drift
    assert "microTremorX" in source
    assert "microTremorY" in source
    assert "gazeCurrentX" in source
    assert "gazeCurrentY" in source

    # Physics-based spring interpolator
    assert "evaluateSpringProgress" in source
    assert "dampingRatio" in source
    assert "angularFreq" in source

    # Pixel shifting for OLED/IPS burn-in protection
    assert "pixelShiftOffsets" in source
    assert "nextPixelShiftTime" in source
    assert "currentShiftX" in source and "currentShiftY" in source

    # Ambient sleep mode (> 5 minutes idle)
    assert "MascotExpression.SLEEPING" in source
    assert "300000L" in source
    assert "isAmbientSleeping" in source
    assert "ambientDimMultiplier" in source


def test_main_activity_hud_pixel_shifting_and_ambient_dim():
    """Verify MainActivity integrates HUD pixel-shifting and ambient sleep dimming."""
    source = (ANDROID_MAIN / "java/id/my/yhdev/nexo/MainActivity.kt").read_text()

    assert "hudShiftOffsets" in source
    assert "pixelShiftRunnable" in source
    assert "ambientCheckRunnable" in source
    assert "resetActivity" in source
    assert "pillStatus.animate()" in source
