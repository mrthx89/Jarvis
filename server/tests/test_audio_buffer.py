"""Tests for CircularAudioBuffer ring buffer."""

import pytest
from nexo_server.gateway.audio_buffer import CircularAudioBuffer, CircularRingBuffer


def test_circular_buffer_init_and_capacity():
    buf = CircularAudioBuffer(capacity=100)
    assert buf.capacity == 100
    assert len(buf) == 0
    assert not buf
    assert buf.get_bytes() == b""

    with pytest.raises(ValueError):
        CircularAudioBuffer(0)
    with pytest.raises(ValueError):
        CircularAudioBuffer(-10)


def test_circular_buffer_write_under_capacity():
    buf = CircularAudioBuffer(capacity=10)
    buf.extend(b"abcd")
    assert len(buf) == 4
    assert bool(buf)
    assert buf.get_bytes() == b"abcd"
    assert bytes(buf) == b"abcd"

    buf.extend(b"ef")
    assert len(buf) == 6
    assert buf.get_bytes() == b"abcdef"


def test_circular_buffer_wrapping_preserves_newest():
    buf = CircularAudioBuffer(capacity=6)
    buf.extend(b"1234")
    assert buf.get_bytes() == b"1234"

    # Now write 4 more bytes: "5678" -> total 8 bytes, only last 6 should remain: "345678"
    buf.extend(b"5678")
    assert len(buf) == 6
    assert buf.get_bytes() == b"345678"
    assert bytes(buf) == b"345678"

    # Write 2 more bytes: "90" -> oldest 2 ("34") dropped, now "567890"
    buf.extend(b"90")
    assert len(buf) == 6
    assert buf.get_bytes() == b"567890"


def test_circular_buffer_oversized_write():
    buf = CircularAudioBuffer(capacity=5)
    buf.extend(b"0123456789abcdef")
    assert len(buf) == 5
    assert buf.get_bytes() == b"bcdef"


def test_circular_buffer_clear_zero_allocation():
    buf = CircularAudioBuffer(capacity=8)
    buf.extend(b"abcdefgh")
    assert len(buf) == 8
    buf.clear()
    assert len(buf) == 0
    assert not buf
    assert buf.get_bytes() == b""

    # Write again after clear
    buf.extend(b"xyz")
    assert len(buf) == 3
    assert buf.get_bytes() == b"xyz"


def test_circular_buffer_bytearray_extend_compatibility():
    # Verify that bytearray.extend accepts CircularAudioBuffer
    buf = CircularAudioBuffer(capacity=10)
    buf.extend(b"test audio")
    destination = bytearray(b"prefix: ")
    destination.extend(buf)
    assert destination == b"prefix: test audio"


def test_circular_buffer_slice_del_compatibility():
    # Backward compatibility with `del s._preroll[:-8000]`
    buf = CircularAudioBuffer(capacity=10)
    buf.extend(b"12345")
    del buf[:-5]
    assert len(buf) == 5
    assert buf.get_bytes() == b"12345"


def test_circular_buffer_indexing():
    buf = CircularAudioBuffer(capacity=4)
    buf.extend(b"abcd")
    buf.extend(b"ef")  # buffer contains b"cdef"
    assert buf[0] == ord("c")
    assert buf[3] == ord("f")


def test_circular_buffer_exact_capacity_boundary():
    buf = CircularAudioBuffer(capacity=5)
    # 1. Exactly capacity
    assert buf.extend(b"12345") == 5
    assert len(buf) == 5
    assert buf.get_bytes() == b"12345"

    # 2. Overwrite by 1 byte
    assert buf.extend(b"6") == 1
    assert len(buf) == 5
    assert buf.get_bytes() == b"23456"

    # 3. Clear and write capacity - 1 then 1 byte
    buf.clear()
    assert buf.extend(b"ABCD") == 4
    assert len(buf) == 4
    assert buf.extend(b"E") == 1
    assert len(buf) == 5
    assert buf.get_bytes() == b"ABCDE"


def test_circular_buffer_multiple_wraparound_cycles_fuzz():
    from collections import deque
    import random

    capacity = 16
    buf = CircularAudioBuffer(capacity=capacity)
    reference = deque(maxlen=capacity)

    rng = random.Random(42)
    for _ in range(300):
        chunk_len = rng.randint(0, 35)
        chunk = bytes([rng.randint(0, 255) for _ in range(chunk_len)])

        buf.extend(chunk)
        reference.extend(chunk)

        assert len(buf) == len(reference)
        assert buf.get_bytes() == bytes(reference)


def test_circular_buffer_negative_indexing_and_slices():
    buf = CircularAudioBuffer(capacity=8)
    buf.extend(b"01234567")
    buf.extend(b"89")  # Now contents are b"23456789"
    assert buf[-1] == ord("9")
    assert buf[-2] == ord("8")
    assert buf[:4] == b"2345"
    assert buf[4:] == b"6789"
    assert list(buf) == list(b"23456789")


def test_circular_buffer_zero_byte_write_is_noop():
    buf = CircularAudioBuffer(capacity=6)
    buf.extend(b"hello")
    assert buf.extend(b"") == 0
    assert len(buf) == 5
    assert buf.get_bytes() == b"hello"


def test_circular_buffer_memory_stability():
    buf = CircularAudioBuffer(capacity=1024)
    original_id = id(buf._buffer)
    for i in range(100):
        buf.extend(b"chunk of audio bytes" * 10)
        assert id(buf._buffer) == original_id
    buf.clear()
    assert id(buf._buffer) == original_id


def test_circular_buffer_dual_compatibility():
    import jarvis_server.gateway.audio_buffer as jarvis_ab
    import nexo_server.gateway.audio_buffer as nexo_ab

    buf1 = jarvis_ab.CircularAudioBuffer(capacity=10)
    buf2 = nexo_ab.CircularAudioBuffer(capacity=10)
    buf1.extend(b"audio_stream")
    buf2.extend(b"audio_stream")
    assert buf1.get_bytes() == buf2.get_bytes()

