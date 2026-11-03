"""Source-level guards only: Android execution is forbidden for this repair."""
from pathlib import Path

SOURCE = (Path(__file__).parents[2] / "android/app/src/main/java/id/my/yhdev/nexo/audio/AudioPlayerManager.kt").read_text()


def test_pcm_overflow_waits_for_capacity_instead_of_dropping_speech():
    write = SOURCE.split("fun writeChunk(", 1)[1].split("/** tts_end", 1)[0]
    assert "dropping chunk" not in write, "Full queue currently deletes spoken PCM"
    assert "bufferLock.wait(" in write
    assert "copyOfRange" in write, "A single message larger than the cap must be split"
    assert "current(token)" in write


def test_old_generation_cannot_subtract_from_new_queue_accounting():
    write = SOURCE.split("fun writeChunk(", 1)[1].split("/** tts_end", 1)[0]
    assert "if (token == generation.get())" in write
    assert "bufferLock.notifyAll()" in write
