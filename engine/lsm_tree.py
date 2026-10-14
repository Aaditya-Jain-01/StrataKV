from __future__ import annotations
import os
import glob
import time
import heapq
from typing import Dict, List, Optional, Tuple, Iterator
from engine.skip_list import SkipListMemTable, TOMBSTONE
from engine.wal import WriteAheadLog
from engine.sstable import SSTableWriter, SSTableReader
from engine.bloom_filter import BloomFilter


class SSTableHandle:
    def __init__(self, base_path: str) -> None:
        self.base_path = base_path
        self.sst_path = f"{base_path}.sst"
        self.idx_path = f"{base_path}.idx"
        self.flt_path = f"{base_path}.filter"
        self.reader = SSTableReader(self.sst_path, self.idx_path)
        with open(self.flt_path, "rb") as f:
            self.bloom_filter = BloomFilter.from_bytes(f.read())


class LSMTree:
    def __init__(
        self,
        data_dir: str = "data_store",
        memtable_threshold_bytes: int = 32 * 1024,
        sparse_interval: int = 16,
    ) -> None:
        self.data_dir = data_dir
        self.memtable_threshold = memtable_threshold_bytes
        self.sparse_interval = sparse_interval
        os.makedirs(self.data_dir, exist_ok=True)

        self.wal_path = os.path.join(self.data_dir, "active.wal")
        self.wal = WriteAheadLog(self.wal_path)
        self.memtable = SkipListMemTable()
        self.sstables: List[SSTableHandle] = []
        self._recover_and_init()

    def _recover_and_init(self) -> None:
        idx_files = sorted(
            glob.glob(os.path.join(self.data_dir, "sst_*.idx")),
            key=lambda x: int(os.path.basename(x).split(".")[0].split("_")[1]),
        )
        for idx_file in idx_files:
            base_path = idx_file[:-4]
            if os.path.exists(f"{base_path}.sst") and os.path.exists(f"{base_path}.filter"):
                self.sstables.append(SSTableHandle(base_path))

        for key, val in self.wal.recover():
            self.memtable.put(key, val)

    def put(self, key: str, value: str) -> None:
        self.wal.append(key, value)
        self.memtable.put(key, value)
        if self.memtable.size_bytes >= self.memtable_threshold:
            self.flush()

    def get(self, key: str) -> Optional[str]:
        val = self.memtable.get(key)
        if val is not None:
            return None if val == TOMBSTONE else val

        for sst in reversed(self.sstables):
            if not sst.bloom_filter.contains(key):
                continue
            res = sst.reader.get(key)
            if res is not None:
                return None if res == TOMBSTONE else res

        return None

    def delete(self, key: str) -> None:
        self.put(key, TOMBSTONE)

    def flush(self) -> Optional[str]:
        if self.memtable.entry_count == 0:
            return None

        timestamp = int(time.time() * 1000)
        base_name = f"sst_{timestamp}_{len(self.sstables)}"
        base_path = os.path.join(self.data_dir, base_name)
        sst_path = f"{base_path}.sst"
        idx_path = f"{base_path}.idx"
        flt_path = f"{base_path}.filter"

        items = list(self.memtable.iter_items())
        bf = BloomFilter(expected_elements=max(len(items), 1), false_positive_rate=0.01)
        for k, _ in items:
            bf.add(k)

        with open(flt_path, "wb") as f:
            f.write(bf.to_bytes())

        SSTableWriter.write(sst_path, idx_path, iter(items), self.sparse_interval)
        self.sstables.append(SSTableHandle(base_path))

        self.memtable.clear()
        self.wal.clear()
        return base_name

    def compact(self) -> None:
        if len(self.sstables) < 2:
            return

        merged_items: Dict[str, str] = {}
        for sst in self.sstables:
            for k, v in sst.reader.scan():
                merged_items[k] = v

        cleaned_items = [(k, v) for k, v in sorted(merged_items.items()) if v != TOMBSTONE]

        timestamp = int(time.time() * 1000)
        compact_base = os.path.join(self.data_dir, f"sst_{timestamp}_compacted")
        sst_path = f"{compact_base}.sst"
        idx_path = f"{compact_base}.idx"
        flt_path = f"{compact_base}.filter"

        bf = BloomFilter(expected_elements=max(len(cleaned_items), 1), false_positive_rate=0.01)
        for k, _ in cleaned_items:
            bf.add(k)

        with open(flt_path, "wb") as f:
            f.write(bf.to_bytes())

        SSTableWriter.write(sst_path, idx_path, iter(cleaned_items), self.sparse_interval)

        for sst in self.sstables:
            for ext in (".sst", ".idx", ".filter"):
                p = f"{sst.base_path}{ext}"
                if os.path.exists(p):
                    os.remove(p)

        self.sstables = [SSTableHandle(compact_base)]

    def close(self) -> None:
        self.wal.close()