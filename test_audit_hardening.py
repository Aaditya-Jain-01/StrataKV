import os
import shutil
import pytest
from engine.skip_list import SkipListMemTable
from engine.bloom_filter import BloomFilter
from engine.lsm_tree import LSMTree, MissingSSTableError

TEST_DIR = "test_audit_store"

def cleanup():
    if os.path.exists(TEST_DIR):
        shutil.rmtree(TEST_DIR, ignore_errors=True)

def run_audit_hardening_tests():
    cleanup()
    print("-" * 65)
    print(" STRATAKV AUDIT HARDENING REGRESSION TEST SUITE ")
    print("-" * 65)

    print("[TEST 1] Testing SkipList parameter & input validation...")
    for bad_level in (0, -1, 100, 3.5):
        try:
            SkipListMemTable(max_level=bad_level)
            assert False, f"Accepted bad max_level: {bad_level}"
        except ValueError:
            pass

    for bad_p in (0.0, 1.0, -0.5, 1.5):
        try:
            SkipListMemTable(p=bad_p)
            assert False, f"Accepted bad p: {bad_p}"
        except ValueError:
            pass

    mem = SkipListMemTable()
    try:
        mem.put("", "val")
        assert False, "Accepted empty key"
    except ValueError:
        pass
    print("  SkipList input and boundary validation verified.")

    print("\n[TEST 2] Testing BloomFilter constructor bounds validation...")
    for bad_elem in (0, -10):
        try:
            BloomFilter(expected_elements=bad_elem)
            assert False, f"Accepted non-positive elements: {bad_elem}"
        except ValueError:
            pass

    for bad_fp in (0.0, 1.0, -0.1, 1.5):
        try:
            BloomFilter(false_positive_rate=bad_fp)
            assert False, f"Accepted bad false_positive_rate: {bad_fp}"
        except ValueError:
            pass
    print("  BloomFilter constructor validation verified.")

    print("\n[TEST 3] Testing monotonic sequence IDs (preventing timestamp collisions)...")
    tree = LSMTree(data_dir=TEST_DIR, memtable_threshold_bytes=100)
    # Rapid burst writes and flushes
    for i in range(5):
        tree.put(f"burst:{i}", f"val_{i}")
        tree.flush()

    assert len(tree.sstables) == 5
    base_names = [os.path.basename(s.base_path) for s in tree.sstables]
    expected_bases = [f"sst_{i:06d}" for i in range(1, 6)]
    assert base_names == expected_bases, f"Mismatch in sequence IDs: {base_names}"
    print(f"  Monotonic sequence IDs strictly generated: {base_names}")
    tree.close()

    print("\n[TEST 4] Testing manifest fail-safe recovery on missing SSTable file...")
    # Intentionally delete the physical .sst data file of table sst_000003
    os.remove(os.path.join(TEST_DIR, "sst_000003.sst"))

    try:
        LSMTree(data_dir=TEST_DIR)
        assert False, "Engine silently started despite missing manifest-referenced SSTable!"
    except MissingSSTableError as e:
        print(f"  Missing referenced table caught with fail-fast exception:\n    {e}")

    cleanup()
    print("-" * 65)
    print(" [STATUS] ALL AUDIT HARDENING ASSERTIONS PASSED SUCCESSFULLY")
    print("-" * 65)

if __name__ == "__main__":
    run_audit_hardening_tests()