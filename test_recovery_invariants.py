import os
import shutil
import struct
import zlib
import pytest
from engine.lsm_tree import LSMTree, CorruptedManifestError
from engine.sstable import SSTableWriter, SSTableReader, CorruptedSSTableError

TEST_DIR = "test_recovery_invariants_store"


def cleanup():
    if os.path.exists(TEST_DIR):
        shutil.rmtree(TEST_DIR, ignore_errors=True)


def run_tests():
    cleanup()
    print("-" * 65)
    print(" STRATAKV AUDIT: RECOVERY SAFETY & INVARIANT REGRESSION TESTS ")
    print("-" * 65)

    print("[TEST 1] Verifying no deleted-record resurrection across compaction & restart...")
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

    # Re-open database from clean manifest
    reopened = LSMTree(data_dir=TEST_DIR, memtable_threshold_bytes=64 * 1024)
    assert reopened.get("user:target") is None, "Deleted record was resurrected after clean restart"
    reopened.close()
    print("  Tombstone compaction verified: key remains completely absent across restarts.")

    print("\n[TEST 2] Verifying malformed manifest raises CorruptedManifestError (no silent fallback)...")
    manifest_path = os.path.join(TEST_DIR, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        f.write("{ INVALID_JSON_BYTES_NOT_PARSED")

    try:
        LSMTree(data_dir=TEST_DIR)
        assert False, "Engine silently fell back to discovery on malformed manifest"
    except CorruptedManifestError:
        print("  Malformed manifest failed fast via CorruptedManifestError.")

    print("\n[TEST 3] Verifying SSTable reader rejects invalid UTF-8 value payloads...")
    cleanup()
    sst_file = os.path.join(TEST_DIR, "test_utf8.sst")
    idx_file = os.path.join(TEST_DIR, "test_utf8.idx")
    os.makedirs(TEST_DIR, exist_ok=True)

    items = [("valid_key", "valid_value")]
    SSTableWriter.write(sst_file, idx_file, iter(items))

    # Overwrite value payload with invalid UTF-8 bytes (0xFF) while keeping CRC valid
    k_bytes = b"valid_key"
    bad_v_bytes = b"\xff\xff\xff"
    payload = k_bytes + bad_v_bytes
    crc = zlib.crc32(payload)
    header = struct.pack("!III", crc, len(k_bytes), len(bad_v_bytes))
    with open(sst_file, "wb") as f:
        f.write(header + payload)

    reader = SSTableReader(sst_file, idx_file)
    try:
        reader.get("valid_key")
        assert False, "Reader failed to raise on non-UTF8 value"
    except CorruptedSSTableError:
        print("  Non-UTF8 value caught and wrapped in CorruptedSSTableError.")

    print("\n[TEST 4] Verifying SSTable reader rejects implausibly large record lengths during index check...")
    sst_len_file = os.path.join(TEST_DIR, "test_len.sst")
    idx_len_file = os.path.join(TEST_DIR, "test_len.idx")

    k_target = b"valid_key"
    with open(idx_len_file, "wb") as f_idx:
        f_idx.write(struct.pack("!IQ", len(k_target), 0) + k_target)

    # Key length = 500 MB (exceeds MAX_KEY_LEN limit of 64 KB)
    with open(sst_len_file, "wb") as f:
        f.write(struct.pack("!III", 12345, 500 * 1024 * 1024, 10))

    try:
        # SSTableReader validates index-to-record boundaries during initialization
        SSTableReader(sst_len_file, idx_len_file)
        assert False, "SSTableReader accepted implausible record length in header"
    except CorruptedSSTableError:
        print("  Implausibly large record length rejected during index alignment check at initialization.")

    cleanup()
    print("-" * 65)
    print(" [STATUS] ALL RECOVERY INVARIANT TESTS PASSED CLEANLY")
    print("-" * 65)


if __name__ == "__main__":
    run_tests()