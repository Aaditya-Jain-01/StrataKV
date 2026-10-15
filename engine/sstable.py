from __future__ import annotations
import os
import struct
import zlib
from typing import Iterator, List, Optional, Tuple


class CorruptedSSTableError(Exception):
    """Raised when an SSTable data file or index is truncated, malformed, or corrupt."""
    pass


class SSTableWriter:
    HEADER_FORMAT = "!III"  # CRC32 (uint32), Key Length (uint32), Value Length (uint32)
    HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
    MAX_KEY_LEN = 64 * 1024             # 64 KB limit
    MAX_VAL_LEN = 64 * 1024 * 1024      # 64 MB limit

    @staticmethod
    def write(
        sst_path: str,
        idx_path: str,
        items: Iterator[Tuple[str, str]],
        sparse_interval: int = 16
    ) -> int:
        count = 0
        sparse_index: List[Tuple[str, int]] = []

        with open(sst_path, "wb") as f_data:
            for key, val in items:
                offset = f_data.tell()
                if count % sparse_interval == 0:
                    sparse_index.append((key, offset))

                key_bytes = key.encode("utf-8")
                val_bytes = val.encode("utf-8")

                if len(key_bytes) > SSTableWriter.MAX_KEY_LEN:
                    raise ValueError(f"Key exceeds maximum supported size: {len(key_bytes)} bytes")
                if len(val_bytes) > SSTableWriter.MAX_VAL_LEN:
                    raise ValueError(f"Value exceeds maximum supported size: {len(val_bytes)} bytes")

                payload = key_bytes + val_bytes
                checksum = zlib.crc32(payload)
                header = struct.pack(
                    SSTableWriter.HEADER_FORMAT, checksum, len(key_bytes), len(val_bytes)
                )
                f_data.write(header + payload)
                count += 1
            f_data.flush()
            os.fsync(f_data.fileno())

        with open(idx_path, "wb") as f_idx:
            for key, offset in sparse_index:
                k_bytes = key.encode("utf-8")
                f_idx.write(struct.pack("!IQ", len(k_bytes), offset) + k_bytes)
            f_idx.flush()
            os.fsync(f_idx.fileno())

        return count


class SSTableReader:
    HEADER_FORMAT = "!III"
    HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
    INDEX_FORMAT = "!IQ"
    INDEX_HEADER_SIZE = struct.calcsize(INDEX_FORMAT)
    MAX_KEY_LEN = 64 * 1024
    MAX_VAL_LEN = 64 * 1024 * 1024

    def __init__(self, sst_path: str, idx_path: str) -> None:
        self.sst_path = sst_path
        self.idx_path = idx_path
        self.sparse_index: List[Tuple[str, int]] = []
        self._load_index()

    def _load_index(self) -> None:
        if not os.path.exists(self.idx_path):
            return

        sst_size = os.path.getsize(self.sst_path) if os.path.exists(self.sst_path) else 0

        with open(self.idx_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            idx_size = f.tell()
            f.seek(0)

            prev_key: Optional[str] = None
            prev_offset: int = -1

            while True:
                curr_pos = f.tell()
                if curr_pos == idx_size:
                    break

                h = f.read(self.INDEX_HEADER_SIZE)
                if len(h) < self.INDEX_HEADER_SIZE:
                    raise CorruptedSSTableError(
                        f"Truncated index header at offset {curr_pos} in {self.idx_path}"
                    )

                k_len, offset = struct.unpack(self.INDEX_FORMAT, h)
                if k_len > self.MAX_KEY_LEN:
                    raise CorruptedSSTableError(f"Implausibly large index key length: {k_len} bytes")

                k_bytes = f.read(k_len)
                if len(k_bytes) < k_len:
                    raise CorruptedSSTableError(
                        f"Truncated index key payload at offset {curr_pos} in {self.idx_path}"
                    )

                try:
                    k_str = k_bytes.decode("utf-8")
                except UnicodeDecodeError as e:
                    raise CorruptedSSTableError(f"Corrupt index key UTF-8 sequence: {e}") from e

                if prev_key is not None and k_str <= prev_key:
                    raise CorruptedSSTableError(
                        f"Index keys not strictly ordered: '{k_str}' follows '{prev_key}'"
                    )
                if offset <= prev_offset:
                    raise CorruptedSSTableError(
                        f"Index offsets not strictly increasing: {offset} follows {prev_offset}"
                    )
                if sst_size > 0 and offset >= sst_size:
                    raise CorruptedSSTableError(
                        f"Index offset {offset} out of bounds for SSTable of size {sst_size}"
                    )

                self.sparse_index.append((k_str, offset))
                prev_key = k_str
                prev_offset = offset

    def get(self, key: str) -> Optional[str]:
        if not self.sparse_index or not os.path.exists(self.sst_path):
            return None

        if key < self.sparse_index[0][0]:
            return None

        low = 0
        high = len(self.sparse_index) - 1
        candidate_idx = 0

        while low <= high:
            mid = (low + high) // 2
            if self.sparse_index[mid][0] <= key:
                candidate_idx = mid
                low = mid + 1
            else:
                high = mid - 1

        start_offset = self.sparse_index[candidate_idx][1]
        end_offset = None
        if candidate_idx + 1 < len(self.sparse_index):
            end_offset = self.sparse_index[candidate_idx + 1][1]

        sst_size = os.path.getsize(self.sst_path)

        with open(self.sst_path, "rb") as f:
            f.seek(start_offset)
            while True:
                curr_offset = f.tell()
                if end_offset is not None and curr_offset >= end_offset:
                    break
                if curr_offset == sst_size:
                    break

                h = f.read(self.HEADER_SIZE)
                if len(h) == 0:
                    break
                if len(h) < self.HEADER_SIZE:
                    raise CorruptedSSTableError(
                        f"Truncated record header at byte {curr_offset} in {self.sst_path}"
                    )

                checksum, k_len, v_len = struct.unpack(self.HEADER_FORMAT, h)
                if k_len > self.MAX_KEY_LEN or v_len > self.MAX_VAL_LEN:
                    raise CorruptedSSTableError(
                        f"Implausibly large record length (key: {k_len}, val: {v_len}) at {curr_offset}"
                    )

                payload = f.read(k_len + v_len)
                if len(payload) < (k_len + v_len):
                    raise CorruptedSSTableError(
                        f"Truncated payload at byte {curr_offset} in {self.sst_path}"
                    )

                if zlib.crc32(payload) != checksum:
                    raise CorruptedSSTableError(
                        f"CRC32 mismatch at byte {curr_offset} in {self.sst_path}"
                    )

                try:
                    curr_key = payload[:k_len].decode("utf-8")
                except UnicodeDecodeError as e:
                    raise CorruptedSSTableError(f"Corrupt key UTF-8 sequence at {curr_offset}: {e}") from e

                if curr_key == key:
                    try:
                        return payload[k_len:].decode("utf-8")
                    except UnicodeDecodeError as e:
                        raise CorruptedSSTableError(f"Corrupt value UTF-8 sequence at {curr_offset}: {e}") from e

                if curr_key > key:
                    break

        return None

    def scan(self) -> Iterator[Tuple[str, str]]:
        if not os.path.exists(self.sst_path):
            return

        sst_size = os.path.getsize(self.sst_path)
        with open(self.sst_path, "rb") as f:
            while True:
                curr_offset = f.tell()
                if curr_offset == sst_size:
                    break

                h = f.read(self.HEADER_SIZE)
                if len(h) == 0:
                    break
                if len(h) < self.HEADER_SIZE:
                    raise CorruptedSSTableError(
                        f"Truncated record header at byte {curr_offset} in {self.sst_path}"
                    )

                checksum, k_len, v_len = struct.unpack(self.HEADER_FORMAT, h)
                if k_len > self.MAX_KEY_LEN or v_len > self.MAX_VAL_LEN:
                    raise CorruptedSSTableError(
                        f"Implausibly large record length (key: {k_len}, val: {v_len}) at {curr_offset}"
                    )

                payload = f.read(k_len + v_len)
                if len(payload) < (k_len + v_len):
                    raise CorruptedSSTableError(
                        f"Truncated record payload at byte {curr_offset} in {self.sst_path}"
                    )

                if zlib.crc32(payload) != checksum:
                    raise CorruptedSSTableError(
                        f"CRC32 mismatch at byte {curr_offset} in {self.sst_path}"
                    )

                try:
                    key = payload[:k_len].decode("utf-8")
                    value = payload[k_len:].decode("utf-8")
                except UnicodeDecodeError as e:
                    raise CorruptedSSTableError(f"Corrupt UTF-8 sequence at {curr_offset}: {e}") from e

                yield key, value