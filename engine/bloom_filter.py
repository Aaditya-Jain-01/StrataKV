import math
import zlib
import struct
import hashlib

class BloomFilter:
    HEADER_FORMAT = "!II"
    HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

    def __init__(self, expected_elements: int = 1000, false_positive_rate: float = 0.01) -> None:
        self.n = max(expected_elements, 1)
        self.p = false_positive_rate
        self.num_bits = int(-(self.n * math.log(self.p)) / (math.log(2) ** 2))
        self.num_bits = max(self.num_bits, 8)
        self.num_hashes = int((self.num_bits / self.n) * math.log(2))
        self.num_hashes = max(self.num_hashes, 1)
        self.num_bytes = (self.num_bits + 7) // 8
        self.bit_array = bytearray(self.num_bytes)

    def _hashes(self, key: str):
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
        num_bits, num_hashes = struct.unpack(cls.HEADER_FORMAT, data[:cls.HEADER_SIZE])
        bf = cls.__new__(cls)
        bf.num_bits = num_bits
        bf.num_hashes = num_hashes
        bf.num_bytes = (num_bits + 7) // 8
        bf.bit_array = bytearray(data[cls.HEADER_SIZE:cls.HEADER_SIZE + bf.num_bytes])
        bf.n = 0
        bf.p = 0.0
        return bf