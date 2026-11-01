"""Circular ring buffer for incoming raw audio PCM chunks."""

from typing import Union


class CircularAudioBuffer:
    """Pre-allocated circular byte ring buffer for zero-allocation audio streaming.

    Eliminates dynamic allocations and heap fragmentation during 24/7 continuous
    audio streaming.
    """

    def __init__(self, capacity: int):
        if capacity <= 0:
            raise ValueError("Capacity must be positive")
        self._capacity = int(capacity)
        self._buffer = bytearray(self._capacity)
        self._head = 0  # Points to next write slot
        self._count = 0  # Number of valid bytes currently buffered

    @property
    def capacity(self) -> int:
        return self._capacity

    def write(self, data: Union[bytes, bytearray, memoryview]) -> int:
        """Write bytes to the buffer, overwriting oldest data if capacity exceeded."""
        return self.extend(data)

    def extend(self, data: Union[bytes, bytearray, memoryview]) -> int:
        """Append bytes to the circular ring buffer."""
        n = len(data)
        if n == 0:
            return 0

        if n >= self._capacity:
            # New data is at least capacity: keep only the newest 'capacity' bytes.
            tail = data[-self._capacity:]
            self._buffer[:] = tail
            self._head = 0
            self._count = self._capacity
            return self._capacity

        first_chunk = min(n, self._capacity - self._head)
        self._buffer[self._head : self._head + first_chunk] = data[:first_chunk]
        second_chunk = n - first_chunk
        if second_chunk > 0:
            self._buffer[:second_chunk] = data[first_chunk:n]

        self._head = (self._head + n) % self._capacity
        self._count = min(self._capacity, self._count + n)
        return n

    def get_bytes(self) -> bytes:
        """Return all buffered bytes in chronological order."""
        if self._count == 0:
            return b""
        if self._count < self._capacity:
            start = (self._head - self._count) % self._capacity
            if start < self._head:
                return bytes(self._buffer[start : self._head])
            return bytes(self._buffer[start : self._capacity]) + bytes(self._buffer[: self._head])
        # Buffer is full: oldest byte is at _head
        return bytes(self._buffer[self._head : self._capacity]) + bytes(self._buffer[: self._head])

    def to_bytes(self) -> bytes:
        return self.get_bytes()

    def clear(self) -> None:
        """Reset buffer pointers without reallocating underlying bytearray."""
        self._head = 0
        self._count = 0

    def __len__(self) -> int:
        return self._count

    def __bool__(self) -> bool:
        return self._count > 0

    def __bytes__(self) -> bytes:
        return self.get_bytes()

    def __iter__(self):
        return iter(self.get_bytes())

    def __getitem__(self, item):
        return self.get_bytes()[item]

    def __delitem__(self, key):
        # Backward compatibility for code doing `del self._preroll[:-self.PREROLL_BYTES]`
        pass

    def __repr__(self) -> str:
        return f"<CircularAudioBuffer count={self._count} capacity={self._capacity}>"


# Aliases for compatibility
CircularRingBuffer = CircularAudioBuffer
AudioRingBuffer = CircularAudioBuffer
