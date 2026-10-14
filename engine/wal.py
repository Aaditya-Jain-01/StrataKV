import os
import struct
import zlib
from typing import Iterator, Tuple

class WriteAheadLog:
    HEADER_FORMAT = "!III"
    HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

    def __init__(self, filepath: str) -> None:
        self.filepath = filepath
        parent_dir = os.path.dirname(self.filepath)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        self.file = open(self.filepath, "a+b")

    def append(self, key: str, value: str) -> None:
        if self.file.closed:
            self.file = open(self.filepath, "a+b")
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

        if not self.file.closed:
            self.file.flush()

        valid_records = []
        valid_offset = 0

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
                try:
                    key = payload[:key_len].decode("utf-8")
                    value = payload[key_len:].decode("utf-8")
                except UnicodeDecodeError:
                    break

                valid_records.append((key, value))
                valid_offset = f.tell()

        file_size = os.path.getsize(self.filepath)
        if valid_offset < file_size:
            if not self.file.closed:
                self.file.close()
            with open(self.filepath, "r+b") as f:
                f.seek(valid_offset)
                f.truncate()
                f.flush()
                os.fsync(f.fileno())
            self.file = open(self.filepath, "a+b")

        for item in valid_records:
            yield item

    def clear(self) -> None:
        if not self.file.closed:
            self.file.close()
        self.file = open(self.filepath, "w+b")
        self.file.flush()
        os.fsync(self.file.fileno())

    def close(self) -> None:
        if not self.file.closed:
            try:
                self.file.flush()
                os.fsync(self.file.fileno())
            except OSError:
                pass
            self.file.close()