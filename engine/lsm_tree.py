from __future__ import annotations
import os
import glob
import time
import json
import threading
from typing import Dict, List, Optional
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
        self._lock = threading.RLock()
        self.data_dir = data_dir
        self.memtable_threshold = memtable_threshold_bytes
        self.sparse_interval = sparse_interval
        os.makedirs(self.data_dir, exist_ok=True)

        self.manifest_path = os.path.join(self.data_dir, "manifest.json")
        self.wal_path = os.path.join(self.data_dir, "active.wal")
        self.wal = WriteAheadLog(self.wal_path)
        self.memtable = SkipListMemTable()
        self.sstables: List[SSTableHandle] = []
        self._recover_and_init()

    def _save_manifest(self, table_bases: List[str]) -> None:
        tmp_path = f"{self.manifest_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(table_bases, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, self.manifest_path)

    def _load_manifest(self) -> Optional[List[str]]:
        if not os.path.exists(self.manifest_path):
            return None
        try:
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def _cleanup_orphaned_files(self, valid_bases: List[str]) -> None:
        valid_set = set(valid_bases)
        for filepath in glob.glob(os.path.join(self.data_dir, "sst_*")):
            filename = os.path.basename(filepath)
            if filename.endswith(".tmp"):
                try:
                    os.remove(filepath)
                except OSError:
                    pass
                continue

            base_name = filename.rsplit(".", 1)[0]
            if base_name not in valid_set:
                try:
                    os.remove(filepath)
                except OSError:
                    pass

    def _recover_and_init(self) -> None:
        with self._lock:
            manifest_bases = self._load_manifest()

            if manifest_bases is not None:
                valid_bases = []
                for base_name in manifest_bases:
                    base_path = os.path.join(self.data_dir, base_name)
                    if (
                        os.path.exists(f"{base_path}.sst")
                        and os.path.exists(f"{base_path}.idx")
                        and os.path.exists(f"{base_path}.filter")
                    ):
                        self.sstables.append(SSTableHandle(base_path))
                        valid_bases.append(base_name)
                self._cleanup_orphaned_files(valid_bases)
            else:
                idx_files = sorted(
                    glob.glob(os.path.join(self.data_dir, "sst_*.idx")),
                    key=lambda x: int(os.path.basename(x).split(".")[0].split("_")[1]),
                )
                valid_bases = []
                for idx_file in idx_files:
                    base_path = idx_file[:-4]
                    if os.path.exists(f"{base_path}.sst") and os.path.exists(f"{base_path}.filter"):
                        self.sstables.append(SSTableHandle(base_path))
                        valid_bases.append(os.path.basename(base_path))
                self._save_manifest(valid_bases)
                self._cleanup_orphaned_files(valid_bases)

            for key, val in self.wal.recover():
                self.memtable.put(key, val)

    def put(self, key: str, value: str) -> None:
        with self._lock:
            self.wal.append(key, value)
            self.memtable.put(key, value)
            if self.memtable.size_bytes >= self.memtable_threshold:
                self.flush()

    def get(self, key: str) -> Optional[str]:
        with self._lock:
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
        with self._lock:
            self.put(key, TOMBSTONE)

    def flush(self) -> Optional[str]:
        with self._lock:
            if self.memtable.entry_count == 0:
                return None

            timestamp = int(time.time() * 1000)
            base_name = f"sst_{timestamp}_{len(self.sstables)}"
            base_path = os.path.join(self.data_dir, base_name)
            
            sst_path = f"{base_path}.sst"
            idx_path = f"{base_path}.idx"
            flt_path = f"{base_path}.filter"

            sst_tmp = f"{sst_path}.tmp"
            idx_tmp = f"{idx_path}.tmp"
            flt_tmp = f"{flt_path}.tmp"

            items = list(self.memtable.iter_items())
            bf = BloomFilter(expected_elements=max(len(items), 1), false_positive_rate=0.01)
            for k, _ in items:
                bf.add(k)

            with open(flt_tmp, "wb") as f:
                f.write(bf.to_bytes())
                f.flush()
                os.fsync(f.fileno())

            SSTableWriter.write(sst_tmp, idx_tmp, iter(items), self.sparse_interval)

            os.replace(flt_tmp, flt_path)
            os.replace(sst_tmp, sst_path)
            os.replace(idx_tmp, idx_path)

            current_bases = [os.path.basename(s.base_path) for s in self.sstables]
            current_bases.append(base_name)
            self._save_manifest(current_bases)

            self.sstables.append(SSTableHandle(base_path))
            self.memtable.clear()
            self.wal.clear()
            return base_name

    def compact(self) -> None:
        with self._lock:
            if len(self.sstables) < 2:
                return

            merged_items: Dict[str, str] = {}
            for sst in self.sstables:
                for k, v in sst.reader.scan():
                    merged_items[k] = v

            cleaned_items = [(k, v) for k, v in sorted(merged_items.items()) if v != TOMBSTONE]

            timestamp = int(time.time() * 1000)
            compact_base_name = f"sst_{timestamp}_compacted"
            compact_base = os.path.join(self.data_dir, compact_base_name)
            
            sst_path = f"{compact_base}.sst"
            idx_path = f"{compact_base}.idx"
            flt_path = f"{compact_base}.filter"

            sst_tmp = f"{sst_path}.tmp"
            idx_tmp = f"{idx_path}.tmp"
            flt_tmp = f"{flt_path}.tmp"

            bf = BloomFilter(expected_elements=max(len(cleaned_items), 1), false_positive_rate=0.01)
            for k, _ in cleaned_items:
                bf.add(k)

            with open(flt_tmp, "wb") as f:
                f.write(bf.to_bytes())
                f.flush()
                os.fsync(f.fileno())

            SSTableWriter.write(sst_tmp, idx_tmp, iter(cleaned_items), self.sparse_interval)

            os.replace(flt_tmp, flt_path)
            os.replace(sst_tmp, sst_path)
            os.replace(idx_tmp, idx_path)

            old_sstables = list(self.sstables)
            self._save_manifest([compact_base_name])
            self.sstables = [SSTableHandle(compact_base)]

            for sst in old_sstables:
                for ext in (".sst", ".idx", ".filter"):
                    p = f"{sst.base_path}{ext}"
                    if os.path.exists(p):
                        try:
                            os.remove(p)
                        except OSError:
                            pass

            self._cleanup_orphaned_files([compact_base_name])

    def close(self) -> None:
        with self._lock:
            self.wal.close()