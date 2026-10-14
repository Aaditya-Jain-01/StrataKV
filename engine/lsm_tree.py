from __future__ import annotations
import os
import glob
import json
import threading
from typing import Any, Dict, List, Optional
from engine.skip_list import SkipListMemTable, TOMBSTONE
from engine.wal import WriteAheadLog
from engine.sstable import SSTableWriter, SSTableReader, CorruptedSSTableError
from engine.bloom_filter import BloomFilter, CorruptedBloomFilterError


class MissingSSTableError(Exception):
    """Raised when an SSTable registered in manifest.json is missing or corrupt on disk."""
    pass


class SSTableHandle:
    def __init__(self, base_path: str) -> None:
        self.base_path = base_path
        self.sst_path = f"{base_path}.sst"
        self.idx_path = f"{base_path}.idx"
        self.flt_path = f"{base_path}.filter"

        for p in (self.sst_path, self.idx_path, self.flt_path):
            if not os.path.exists(p):
                raise MissingSSTableError(f"Required storage component missing: {p}")

        self.reader = SSTableReader(self.sst_path, self.idx_path)
        with open(self.flt_path, "rb") as f:
            self.bloom_filter = BloomFilter.from_bytes(f.read())


def _sync_directory(dir_path: str) -> None:
    """Durably sync directory metadata to disk on POSIX systems; safe no-op on Windows."""
    if os.name != "nt":
        try:
            fd = os.open(dir_path, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            pass


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
        _sync_directory(self.data_dir)

        self.manifest_path = os.path.join(self.data_dir, "manifest.json")
        self.wal_path = os.path.join(self.data_dir, "active.wal")
        self.next_file_seq = 1
        self.sstables: List[SSTableHandle] = []
        self.memtable = SkipListMemTable()
        self.wal = WriteAheadLog(self.wal_path)

        self._recover_and_init()

    def _save_manifest(self, table_bases: List[str], next_seq: int) -> None:
        payload = {
            "version": 1,
            "next_seq": next_seq,
            "sstables": table_bases,
        }
        tmp_path = f"{self.manifest_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.flush()
            os.fsync(f.fileno())

        os.replace(tmp_path, self.manifest_path)
        _sync_directory(self.data_dir)

    def _load_manifest(self) -> Optional[Dict[str, Any]]:
        if not os.path.exists(self.manifest_path):
            return None
        try:
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "sstables" in data and "next_seq" in data:
                return data
            # Backwards compatibility if manifest was a raw list
            if isinstance(data, list):
                return {"version": 1, "next_seq": 1000, "sstables": data}
            return None
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
        _sync_directory(self.data_dir)

    def _recover_and_init(self) -> None:
        with self._lock:
            manifest_data = self._load_manifest()

            if manifest_data is not None:
                self.next_file_seq = manifest_data.get("next_seq", 1)
                declared_bases: List[str] = manifest_data.get("sstables", [])

                for base_name in declared_bases:
                    base_path = os.path.join(self.data_dir, base_name)
                    # Missing component referenced by manifest raises fail-fast error
                    try:
                        self.sstables.append(SSTableHandle(base_path))
                    except (MissingSSTableError, CorruptedSSTableError, CorruptedBloomFilterError) as e:
                        raise MissingSSTableError(
                            f"Fatal manifest recovery failure: table '{base_name}' is declared "
                            f"in manifest but cannot be loaded: {e}"
                        ) from e

                self._cleanup_orphaned_files(declared_bases)
            else:
                idx_files = sorted(
                    glob.glob(os.path.join(self.data_dir, "sst_*.idx")),
                    key=lambda x: int(os.path.basename(x).split(".")[0].split("_")[1])
                    if "_" in os.path.basename(x) else 0,
                )
                valid_bases = []
                for idx_file in idx_files:
                    base_path = idx_file[:-4]
                    if os.path.exists(f"{base_path}.sst") and os.path.exists(f"{base_path}.filter"):
                        self.sstables.append(SSTableHandle(base_path))
                        valid_bases.append(os.path.basename(base_path))

                self.next_file_seq = len(valid_bases) + 1
                self._save_manifest(valid_bases, self.next_file_seq)
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

            # Scan from newest to oldest SSTable
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

            seq = self.next_file_seq
            self.next_file_seq += 1
            base_name = f"sst_{seq:06d}"
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
            _sync_directory(self.data_dir)

            current_bases = [os.path.basename(s.base_path) for s in self.sstables]
            current_bases.append(base_name)
            self._save_manifest(current_bases, self.next_file_seq)

            self.sstables.append(SSTableHandle(base_path))
            self.memtable.clear()
            self.wal.clear()
            return base_name

    def compact(self) -> None:
        with self._lock:
            if len(self.sstables) < 2:
                return

            # Chronological merge: oldest to newest so newer keys overwrite older
            merged_items: Dict[str, str] = {}
            for sst in self.sstables:
                for k, v in sst.reader.scan():
                    merged_items[k] = v

            cleaned_items = [(k, v) for k, v in sorted(merged_items.items()) if v != TOMBSTONE]

            seq = self.next_file_seq
            self.next_file_seq += 1
            compact_base_name = f"sst_{seq:06d}_compacted"
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
            _sync_directory(self.data_dir)

            old_sstables = list(self.sstables)
            self._save_manifest([compact_base_name], self.next_file_seq)
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