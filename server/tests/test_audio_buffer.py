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
