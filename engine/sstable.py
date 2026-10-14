from __future__ import annotations
import os
import struct
from typing import Iterator, List, Optional, Tuple

class SSTableWriter:
    HEADER_FORMAT = "!II"
    HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

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
                header = struct.pack(SSTableWriter.HEADER_FORMAT, len(key_bytes), len(val_bytes))
                f_data.write(header + key_bytes + val_bytes)
                count += 1

        with open(idx_path, "wb") as f_idx:
            for key, offset in sparse_index:
                k_bytes = key.encode("utf-8")
                f_idx.write(struct.pack("!IQ", len(k_bytes), offset) + k_bytes)

        return count


class SSTableReader:
    HEADER_FORMAT = "!II"
    HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
    INDEX_FORMAT = "!IQ"
    INDEX_HEADER_SIZE = struct.calcsize(INDEX_FORMAT)

    def __init__(self, sst_path: str, idx_path: str) -> None:
        self.sst_path = sst_path
        self.idx_path = idx_path
        self.sparse_index: List[Tuple[str, int]] = []
        self._load_index()

    def _load_index(self) -> None:
        if not os.path.exists(self.idx_path):
            return
        with open(self.idx_path, "rb") as f:
            while True:
                h = f.read(self.INDEX_HEADER_SIZE)
                if len(h) < self.INDEX_HEADER_SIZE:
                    break
                k_len, offset = struct.unpack(self.INDEX_FORMAT, h)
                k_bytes = f.read(k_len)
                self.sparse_index.append((k_bytes.decode("utf-8"), offset))

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

        with open(self.sst_path, "rb") as f:
            f.seek(start_offset)
            while True:
                curr_offset = f.tell()
                if end_offset is not None and curr_offset >= end_offset:
                    break

                h = f.read(self.HEADER_SIZE)
                if len(h) < self.HEADER_SIZE:
                    break

                k_len, v_len = struct.unpack(self.HEADER_FORMAT, h)
                payload = f.read(k_len + v_len)
                if len(payload) < (k_len + v_len):
                    break

                curr_key = payload[:k_len].decode("utf-8")
                if curr_key == key:
                    return payload[k_len:].decode("utf-8")
                if curr_key > key:
                    break

        return None

    def scan(self) -> Iterator[Tuple[str, str]]:
        if not os.path.exists(self.sst_path):
            return
        with open(self.sst_path, "rb") as f:
            while True:
                h = f.read(self.HEADER_SIZE)
                if len(h) < self.HEADER_SIZE:
                    break
                k_len, v_len = struct.unpack(self.HEADER_FORMAT, h)
                payload = f.read(k_len + v_len)
                if len(payload) < (k_len + v_len):
                    break
                yield payload[:k_len].decode("utf-8"), payload[k_len:].decode("utf-8")