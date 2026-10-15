import os
import shutil
import json
import struct
import zlib
import pytest
from engine.lsm_tree import LSMTree, CorruptedManifestError
from engine.sstable import SSTableWriter, SSTableReader, CorruptedSSTableError

TEST_DIR = "test_manifest_index_hardening_store"


def cleanup():
    if os.path.exists(TEST_DIR):
        shutil.rmtree(TEST_DIR, ignore_errors=True)


def run_tests():
    cleanup()
    print("-" * 65)
    print(" STRATAKV HARDENING: MANIFEST & INDEX ALIGNMENT SUITE ")
    print("-" * 65)

    print("[TEST 1] Missing manifest in genuinely empty directory initializes cleanly...")
    tree = LSMTree(data_dir=TEST_DIR)
    assert os.path.exists(os.path.join(TEST_DIR, "manifest.json"))
    assert len(tree.sstables) == 0
    tree.close()
    print("  Clean fresh directory initialization confirmed.")

    print("\n[TEST 2] Missing manifest with existing SSTables raises CorruptedManifestError...")
    # Add data and flush an SSTable
    tree = LSMTree(data_dir=TEST_DIR)
    tree.put("key:101", "val_101")
    tree.flush()
    tree.close()

    # Delete manifest.json while physical SSTable remains on disk
    os.remove(os.path.join(TEST_DIR, "manifest.json"))
    try:
        LSMTree(data_dir=TEST_DIR)
        assert False, "Engine silently treated non-empty store as fresh, ignoring existing data"
    except CorruptedManifestError:
        print("  Missing manifest in non-empty directory caught cleanly.")

    print("\n[TEST 3] Manifest schema validation tests...")
    cleanup()
    os.makedirs(TEST_DIR, exist_ok=True)
    manifest_file = os.path.join(TEST_DIR, "manifest.json")

    bad_schemas = [
        {"version": 2, "next_seq": 1, "sstables": []},                      # Unsupported version
        {"version": 1, "next_seq": -5, "sstables": []},                     # Negative sequence
        {"version": 1, "next_seq": 1, "sstables": ["../escape"]},           # Path traversal
        {"version": 1, "next_seq": 1, "sstables": ["sub/folder"]},          # Path separator
        {"version": 1, "next_seq": 1, "sstables": ["dup_table", "dup_table"]} # Duplicate table
    ]

    for bad in bad_schemas:
        with open(manifest_file, "w", encoding="utf-8") as f:
            json.dump(bad, f)
        try:
            LSMTree(data_dir=TEST_DIR)
            assert False, f"Accepted invalid manifest schema: {bad}"
        except CorruptedManifestError:
            pass
    print("  All schema violations (versions, traversal, duplicates) rejected.")

    print("\n[TEST 4] SSTable index offset pointing to wrong key raises CorruptedSSTableError...")
    cleanup()
    os.makedirs(TEST_DIR, exist_ok=True)
    sst_path = os.path.join(TEST_DIR, "test_misalign.sst")
    idx_path = os.path.join(TEST_DIR, "test_misalign.idx")

    # Write legitimate record for key "actual_key" at offset 0
    k_actual = b"actual_key"
    v_actual = b"actual_val"
    payload = k_actual + v_actual
    header = struct.pack("!III", zlib.crc32(payload), len(k_actual), len(v_actual))
    with open(sst_path, "wb") as f_sst:
        f_sst.write(header + payload)

    # Write index declaring that offset 0 belongs to key "claimed_key"
    k_claimed = b"claimed_key"
    with open(idx_path, "wb") as f_idx:
        f_idx.write(struct.pack("!IQ", len(k_claimed), 0) + k_claimed)

    try:
        SSTableReader(sst_path, idx_path)
        assert False, "Reader accepted index referencing an unaligned/mismatched key"
    except CorruptedSSTableError as e:
        print("  Index misalignment caught at load time via CorruptedSSTableError.")

    print("\n[TEST 5] SSTable index offset pointing to middle of record...")
    with open(idx_path, "wb") as f_idx:
        f_idx.write(struct.pack("!IQ", len(k_actual), 5) + k_actual)  # Offset 5 lands inside header

    try:
        SSTableReader(sst_path, idx_path)
        assert False, "Reader accepted offset landing inside record body"
    except CorruptedSSTableError:
        print("  Index offset landing in record body caught at load time.")

    cleanup()
    print("-" * 65)
    print(" [STATUS] ALL MANIFEST & INDEX HARDENING ASSERTIONS PASSED ")
    print("-" * 65)


if __name__ == "__main__":
    run_tests()