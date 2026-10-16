import os
import shutil
import struct
import zlib
import pytest
from engine.lsm_tree import LSMTree, CorruptedManifestError
from engine.sstable import SSTableWriter, SSTableReader, CorruptedSSTableError

TEST_DIR = "test_recovery_invariants_store"


@pytest.fixture(autouse=True)
def cleanup():
    """Ensure clean directory state before and after every test."""
    if os.path.exists(TEST_DIR):
        shutil.rmtree(TEST_DIR, ignore_errors=True)
    yield
    if os.path.exists(TEST_DIR):
        shutil.rmtree(TEST_DIR, ignore_errors=True)


def test_no_deleted_record_resurrection_across_compaction_and_restart():
    """Verifies tombstone compaction drops deleted keys permanently across restarts."""
    tree = LSMTree(data_dir=TEST_DIR, memtable_threshold_bytes=64 * 1024)
    tree.put("user:target", "initial_active_value")
    tree.flush()

    tree.delete("user:target")
    tree.flush()
    assert len(tree.sstables) == 2

    tree.compact()
    assert len(tree.sstables) == 1
    assert tree.get("user:target") is None
    tree.close()

    reopened = LSMTree(data_dir=TEST_DIR, memtable_threshold_bytes=64 * 1024)
    assert reopened.get("user:target") is None, "Deleted record was resurrected after clean restart"
    reopened.close()


def test_malformed_manifest_raises_corrupted_manifest_error():
    """Verifies unparseable JSON manifest fails fast via CorruptedManifestError."""
    os.makedirs(TEST_DIR, exist_ok=True)
    manifest_path = os.path.join(TEST_DIR, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        f.write("{ INVALID_JSON_BYTES_NOT_PARSED")

    with pytest.raises(CorruptedManifestError):
        LSMTree(data_dir=TEST_DIR)


def test_sstable_reader_rejects_invalid_utf8_value_payload():
    """Verifies non-UTF8 value payloads are caught and wrapped in CorruptedSSTableError."""
    os.makedirs(TEST_DIR, exist_ok=True)
    sst_file = os.path.join(TEST_DIR, "test_utf8.sst")
    idx_file = os.path.join(TEST_DIR, "test_utf8.idx")

    items = [("valid_key", "valid_value")]
    SSTableWriter.write(sst_file, idx_file, iter(items))

    k_bytes = b"valid_key"
    bad_v_bytes = b"\xff\xff\xff"
    payload = k_bytes + bad_v_bytes
    crc = zlib.crc32(payload)
    header = struct.pack("!III", crc, len(k_bytes), len(bad_v_bytes))
    with open(sst_file, "wb") as f:
        f.write(header + payload)

    reader = SSTableReader(sst_file, idx_file)
    with pytest.raises(CorruptedSSTableError):
        reader.get("valid_key")


def test_sstable_reader_rejects_implausibly_large_record_lengths():
    """Verifies SSTableReader rejects implausible record lengths during index check."""
    os.makedirs(TEST_DIR, exist_ok=True)
    sst_len_file = os.path.join(TEST_DIR, "test_len.sst")
    idx_len_file = os.path.join(TEST_DIR, "test_len.idx")

    k_target = b"valid_key"
    with open(idx_len_file, "wb") as f_idx:
        f_idx.write(struct.pack("!IQ", len(k_target), 0) + k_target)

    with open(sst_len_file, "wb") as f:
        f.write(struct.pack("!III", 12345, 500 * 1024 * 1024, 10))

    with pytest.raises(CorruptedSSTableError):
        SSTableReader(sst_len_file, idx_len_file)


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))