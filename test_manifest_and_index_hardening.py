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


@pytest.fixture(autouse=True)
def cleanup():
    """Ensure clean directory state before and after every test."""
    if os.path.exists(TEST_DIR):
        shutil.rmtree(TEST_DIR, ignore_errors=True)
    yield
    if os.path.exists(TEST_DIR):
        shutil.rmtree(TEST_DIR, ignore_errors=True)


def test_missing_manifest_in_empty_directory_initializes_cleanly():
    """Verifies fresh engine initialization creates manifest in an empty store."""
    tree = LSMTree(data_dir=TEST_DIR)
    assert os.path.exists(os.path.join(TEST_DIR, "manifest.json"))
    assert len(tree.sstables) == 0
    tree.close()


def test_missing_manifest_with_existing_sstables_raises_corrupted_manifest_error():
    """Verifies non-empty directory missing manifest fails fast instead of wiping data."""
    tree = LSMTree(data_dir=TEST_DIR)
    tree.put("key:101", "val_101")
    tree.flush()
    tree.close()

    os.remove(os.path.join(TEST_DIR, "manifest.json"))
    with pytest.raises(CorruptedManifestError):
        LSMTree(data_dir=TEST_DIR)


def test_manifest_schema_validation():
    """Verifies schema validation rejects invalid versions, sequences, and traversal paths."""
    os.makedirs(TEST_DIR, exist_ok=True)
    manifest_file = os.path.join(TEST_DIR, "manifest.json")

    bad_schemas = [
        {"version": 2, "next_seq": 1, "sstables": []},
        {"version": 1, "next_seq": -5, "sstables": []},
        {"version": 1, "next_seq": 1, "sstables": ["../escape"]},
        {"version": 1, "next_seq": 1, "sstables": ["sub/folder"]},
        {"version": 1, "next_seq": 1, "sstables": ["dup_table", "dup_table"]},
    ]

    for bad in bad_schemas:
        with open(manifest_file, "w", encoding="utf-8") as f:
            json.dump(bad, f)
        with pytest.raises(CorruptedManifestError):
            LSMTree(data_dir=TEST_DIR)


def test_sstable_index_offset_pointing_to_wrong_key_raises_corrupted_sstable_error():
    """Verifies index referencing a misaligned or mismatched key raises CorruptedSSTableError."""
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

    with pytest.raises(CorruptedSSTableError):
        SSTableReader(sst_path, idx_path)


def test_sstable_index_offset_pointing_to_middle_of_record():
    """Verifies index offset pointing into record payload body raises CorruptedSSTableError."""
    os.makedirs(TEST_DIR, exist_ok=True)
    sst_path = os.path.join(TEST_DIR, "test_mid_record.sst")
    idx_path = os.path.join(TEST_DIR, "test_mid_record.idx")

    k_actual = b"actual_key"
    v_actual = b"actual_val"
    payload = k_actual + v_actual
    header = struct.pack("!III", zlib.crc32(payload), len(k_actual), len(v_actual))
    with open(sst_path, "wb") as f_sst:
        f_sst.write(header + payload)

    with open(idx_path, "wb") as f_idx:
        f_idx.write(struct.pack("!IQ", len(k_actual), 5) + k_actual)

    with pytest.raises(CorruptedSSTableError):
        SSTableReader(sst_path, idx_path)


def test_sequence_numbers_ge_1000000_pass_schema_validation():
    """Verifies sequence numbers >= 1,000,000 load and validate cleanly."""
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


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))