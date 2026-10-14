import os
import struct
import zlib
from typing import Iterator, Tuple

class WriteAheadLog:
    HEADER_FORMAT = "!III"
    HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

    def __init__(self, filepath: str) -> None:
        self.filepath = filepath
        self.file = open(self.filepath, "a+b")

    def append(self, key: str, value: str) -> None:
        key_bytes = key.encode("utf-8")
        val_bytes = value.encode("utf-8")
        payload = key_bytes + val_bytes
        checksum = zlib.crc32(payload)
        header = struct.pack(self.HEADER_FORMAT, checksum, len(key_bytes), len(val_bytes))
        self.file.write(header + payload)
        self.file.flush()
        os.fsync(self.file.fileno())

    def recover(self) -> Iterator[Tuple[str, str]]:
        if not os.path.exists(self.filepath):
            return
        with open(self.filepath, "rb") as f:
            while True:
                header_bytes = f.read(self.HEADER_SIZE)
                if len(header_bytes) < self.HEADER_SIZE:
                    break
                checksum, key_len, val_len = struct.unpack(self.HEADER_FORMAT, header_bytes)
                payload = f.read(key_len + val_len)
                if len(payload) < (key_len + val_len):
                    break
                if zlib.crc32(payload) != checksum:
                    break
                key = payload[:key_len].decode("utf-8")
                value = payload[key_len:].decode("utf-8")
                yield key, value

    def clear(self) -> None:
        self.file.close()
        self.file = open(self.filepath, "w+b")

    def close(self) -> None:
        if not self.file.closed:
            self.file.close()