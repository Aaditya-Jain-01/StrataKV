import os
import shutil
import pytest
from engine.skip_list import SkipListMemTable
from engine.bloom_filter import BloomFilter
from engine.lsm_tree import LSMTree, MissingSSTableError

TEST_DIR = "test_audit_store"


@pytest.fixture(autouse=True)
def cleanup():
    """Ensure clean directory state before and after every test."""
    if os.path.exists(TEST_DIR):
        shutil.rmtree(TEST_DIR, ignore_errors=True)
    yield
    if os.path.exists(TEST_DIR):
        shutil.rmtree(TEST_DIR, ignore_errors=True)


def test_skiplist_parameter_and_bounds_validation():
    """Verifies SkipList constructor bounds and empty key rejections."""
    for bad_level in (0, -1, 100, 3.5):
        with pytest.raises(ValueError):
            SkipListMemTable(max_level=bad_level)

    for bad_p in (0.0, 1.0, -0.5, 1.5):
        with pytest.raises(ValueError):
            SkipListMemTable(p=bad_p)

    mem = SkipListMemTable()
    with pytest.raises(ValueError):
        mem.put("", "val")


def test_bloom_filter_constructor_bounds_validation():
    """Verifies BloomFilter constructor rejects invalid parameters."""
    for bad_elem in (0, -10):
        with pytest.raises(ValueError):
            BloomFilter(expected_elements=bad_elem)

    for bad_fp in (0.0, 1.0, -0.1, 1.5):
        with pytest.raises(ValueError):
            BloomFilter(false_positive_rate=bad_fp)


def test_monotonic_sequence_ids():
    """Verifies monotonic sequence ID generation across flush cycles."""
    tree = LSMTree(data_dir=TEST_DIR, memtable_threshold_bytes=1024)
    for i in range(5):
        tree.put(f"burst:{i}", f"val_{i}")
        tree.flush()

    assert len(tree.sstables) == 5
    base_names = [os.path.basename(s.base_path) for s in tree.sstables]
    expected_bases = [f"sst_{i:06d}" for i in range(1, 6)]
    assert base_names == expected_bases, f"Mismatch in sequence IDs: {base_names}"
    tree.close()


def test_manifest_fail_safe_recovery_on_missing_sstable():
    """Verifies fail-fast exception when manifest references a missing SSTable."""
    tree = LSMTree(data_dir=TEST_DIR, memtable_threshold_bytes=1024)
    for i in range(5):
        tree.put(f"burst:{i}", f"val_{i}")
        tree.flush()
    tree.close()

    # Intentionally remove a referenced SSTable to simulate corruption / missing file
    target_sst = os.path.join(TEST_DIR, "sst_000003.sst")
    assert os.path.exists(target_sst), "Setup failed: sst_000003.sst should exist before deletion"
    os.remove(target_sst)

    with pytest.raises(MissingSSTableError):
        LSMTree(data_dir=TEST_DIR, memtable_threshold_bytes=1024)


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))