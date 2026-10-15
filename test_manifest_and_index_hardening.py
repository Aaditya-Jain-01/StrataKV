import os
import shutil
import json
import struct
import zlib
import pytest
from engine.lsm_tree import LSMTree, CorruptedManifestError
from engine.sstable import SSTableWriter, SSTableReader, CorruptedSSTableError
from engine.bloom_filter import BloomFilter

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
    tree = LSMTree(data_dir=TEST_DIR)
    tree.put("key:101", "val_101")
    tree.flush()
    tree.close()

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
        {"version": 2, "next_seq": 1, "sstables": []},
        {"version": 1, "next_seq": -5, "sstables": []},
        {"version": 1, "next_seq": 1, "sstables": ["../escape"]},
        {"version": 1, "next_seq": 1, "sstables": ["sub/folder"]},
        {"version": 1, "next_seq": 1, "sstables": ["dup_table", "dup_table"]}
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

    k_actual = b"actual_key"
    v_actual = b"actual_val"
    payload = k_actual + v_actual
    header = struct.pack("!III", zlib.crc32(payload), len(k_actual), len(v_actual))
    with open(sst_path, "wb") as f_sst:
        f_sst.write(header + payload)

    k_claimed = b"claimed_key"
    with open(idx_path, "wb") as f_idx:
        f_idx.write(struct.pack("!IQ", len(k_claimed), 0) + k_claimed)

    try:
        SSTableReader(sst_path, idx_path)
        assert False, "Reader accepted index referencing an unaligned/mismatched key"
    except CorruptedSSTableError:
        print("  Index misalignment caught at load time via CorruptedSSTableError.")

    print("\n[TEST 5] SSTable index offset pointing to middle of record...")
    with open(idx_path, "wb") as f_idx:
        f_idx.write(struct.pack("!IQ", len(k_actual), 5) + k_actual)

    try:
        SSTableReader(sst_path, idx_path)
        assert False, "Reader accepted offset landing inside record body"
    except CorruptedSSTableError:
        print("  Index offset landing in record body caught at load time.")

    print("\n[TEST 6] Verifying sequence numbers >= 1,000,000 pass schema validation...")
    cleanup()
    os.makedirs(TEST_DIR, exist_ok=True)
    manifest_file = os.path.join(TEST_DIR, "manifest.json")

    large_seq_schema = {
        "version": 1,
        "next_seq": 1000001,
        "sstables": ["sst_1000000", "sst_1000000_compacted"],
    }
    with open(manifest_file, "w", encoding="utf-8") as f:
        json.dump(large_seq_schema, f)

    for base in ["sst_1000000", "sst_1000000_compacted"]:
        sst_p = os.path.join(TEST_DIR, f"{base}.sst")
        idx_p = os.path.join(TEST_DIR, f"{base}.idx")
        flt_p = os.path.join(TEST_DIR, f"{base}.filter")
        SSTableWriter.write(sst_p, idx_p, iter([("k1", "v1")]))
        bf = BloomFilter(expected_elements=1)
        bf.add("k1")
        with open(flt_p, "wb") as f_flt:
            f_flt.write(bf.to_bytes())

    tree = LSMTree(data_dir=TEST_DIR)
    assert len(tree.sstables) == 2
    assert tree.next_file_seq == 1000001
    tree.close()
    print("  Sequence numbers >= 1,000,000 loaded cleanly.")

    cleanup()
    print("-" * 65)
    print(" [STATUS] ALL MANIFEST & INDEX HARDENING ASSERTIONS PASSED ")
    print("-" * 65)


if __name__ == "__main__":
    run_tests()