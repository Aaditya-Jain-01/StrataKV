import math
import zlib
import struct
import hashlib
from typing import Iterator


class CorruptedBloomFilterError(Exception):
    """Raised when serialized Bloom filter binary data is invalid, truncated, or corrupt."""
    pass


class BloomFilter:
    HEADER_FORMAT = "!II"  # num_bits (uint32), num_hashes (uint32)
    HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
    MAX_BITS = 128 * 1024 * 1024  # 128 Mbits (~16 MB safety limit)
    MAX_HASHES = 100

    def __init__(self, expected_elements: int = 1000, false_positive_rate: float = 0.01) -> None:
        if not isinstance(expected_elements, int) or expected_elements <= 0:
            raise ValueError(f"expected_elements must be a positive integer, got {expected_elements}")
        if not (0.0 < false_positive_rate < 1.0):
            raise ValueError(f"false_positive_rate must be in (0.0, 1.0), got {false_positive_rate}")

        self.n = expected_elements
        self.p = false_positive_rate
        self.num_bits = int(-(self.n * math.log(self.p)) / (math.log(2) ** 2))
        self.num_bits = max(self.num_bits, 8)
        self.num_hashes = int((self.num_bits / self.n) * math.log(2))
        self.num_hashes = max(min(self.num_hashes, self.MAX_HASHES), 1)
        self.num_bytes = (self.num_bits + 7) // 8
        self.bit_array = bytearray(self.num_bytes)

    def _hashes(self, key: str) -> Iterator[int]:
        key_bytes = key.encode("utf-8")
        h1 = zlib.crc32(key_bytes)
        h2 = int(hashlib.md5(key_bytes).hexdigest()[:8], 16)
        for i in range(self.num_hashes):
            yield (h1 + i * h2) % self.num_bits

    def add(self, key: str) -> None:
        for bit_index in self._hashes(key):
            byte_idx = bit_index // 8
            bit_offset = bit_index % 8
            self.bit_array[byte_idx] |= (1 << bit_offset)

    def contains(self, key: str) -> bool:
        for bit_index in self._hashes(key):
            byte_idx = bit_index // 8
            bit_offset = bit_index % 8
            if not (self.bit_array[byte_idx] & (1 << bit_offset)):
                return False
        return True

    def to_bytes(self) -> bytes:
        header = struct.pack(self.HEADER_FORMAT, self.num_bits, self.num_hashes)
        return header + bytes(self.bit_array)

    @classmethod
    def from_bytes(cls, data: bytes) -> "BloomFilter":
        if len(data) < cls.HEADER_SIZE:
            raise CorruptedBloomFilterError(
                f"Binary data truncated: expected at least {cls.HEADER_SIZE} bytes for header, got {len(data)}"
            )

        num_bits, num_hashes = struct.unpack(cls.HEADER_FORMAT, data[:cls.HEADER_SIZE])

        if num_bits < 8 or num_bits > cls.MAX_BITS:
            raise CorruptedBloomFilterError(
                f"Invalid num_bits value: {num_bits} (must be between 8 and {cls.MAX_BITS})"
            )

        if num_hashes < 1 or num_hashes > cls.MAX_HASHES:
            raise CorruptedBloomFilterError(
                f"Invalid num_hashes value: {num_hashes} (must be between 1 and {cls.MAX_HASHES})"
            )

        expected_payload_bytes = (num_bits + 7) // 8
        actual_payload = data[cls.HEADER_SIZE:]

        if len(actual_payload) != expected_payload_bytes:
            raise CorruptedBloomFilterError(
                f"Payload size mismatch: declared {expected_payload_bytes} bytes from header, "
                f"found {len(actual_payload)} bytes"
            )

        bf = cls.__new__(cls)
        bf.num_bits = num_bits
        bf.num_hashes = num_hashes
        bf.num_bytes = expected_payload_bytes
        bf.bit_array = bytearray(actual_payload)
        bf.n = 0
        bf.p = 0.0
        return bf