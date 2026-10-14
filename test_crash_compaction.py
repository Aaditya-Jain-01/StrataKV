import os
import shutil
import glob
from engine.lsm_tree import LSMTree

TEST_DIR = "test_compaction_crash_store"

def cleanup():
    if os.path.exists(TEST_DIR):
        shutil.rmtree(TEST_DIR, ignore_errors=True)

def run_compaction_crash_tests():
    cleanup()
    print("-" * 65)
    print(" STRATAKV FAULT INJECTION: CRASH-SAFE COMPACTION ")
    print("-" * 65)

    print("[TEST 1] Creating baseline state across multiple flushed SSTables...")
    tree = LSMTree(data_dir=TEST_DIR, memtable_threshold_bytes=64 * 1024)
    
    for i in range(50):
        tree.put(f"user:{i:04d}", f"val_{i}")
    tree.flush()

    for i in range(50, 100):
        tree.put(f"user:{i:04d}", f"val_{i}")
    tree.flush()

    for i in range(0, 20):
        tree.delete(f"user:{i:04d}")
    tree.flush()

    pre_crash_tables = len(tree.sstables)
    assert pre_crash_tables == 3, f"Expected 3 SSTables, found {pre_crash_tables}"
    print(f"  Setup complete with {pre_crash_tables} active SSTables.")

    print("\n[TEST 2] Simulating hard crash mid-compaction (after write, before unlink)...")
    original_remove = os.remove

    def crash_during_unlink(path):
        raise RuntimeError("SIMULATED_POWER_CUT_DURING_COMPACTION")

    os.remove = crash_during_unlink
    try:
        tree.compact()
    except RuntimeError:
        pass
    finally:
        os.remove = original_remove

    tree.close()

    raw_idx_files = glob.glob(os.path.join(TEST_DIR, "sst_*.idx"))
    print(f"  Physical SSTable files lingering on disk: {len(raw_idx_files)} (1 compacted + 3 unlinked)")
    assert len(raw_idx_files) > 1

    print("\n[TEST 3] Recovering engine and asserting manifest isolation...")
    recovered_tree = LSMTree(data_dir=TEST_DIR, memtable_threshold_bytes=64 * 1024)

    assert len(recovered_tree.sstables) == 1, f"Expected 1 compacted table, got {len(recovered_tree.sstables)}"
    assert "compacted" in recovered_tree.sstables[0].base_path
    print("  Engine isolated active set to exactly 1 compacted table via Manifest.")

    print("\n[TEST 4] Verifying data integrity and orphan file sweep...")
    for i in range(0, 20):
        assert recovered_tree.get(f"user:{i:04d}") is None

    for i in range(20, 100):
        assert recovered_tree.get(f"user:{i:04d}") == f"val_{i}"

    surviving_idx_files = glob.glob(os.path.join(TEST_DIR, "sst_*.idx"))
    assert len(surviving_idx_files) == 1, f"Expected 1 surviving index file, found {len(surviving_idx_files)}"
    print("  All orphaned pre-crash SSTable files swept cleanly from disk.")
    print("  All key-value invariants verified intact.")

    recovered_tree.close()
    cleanup()
    print("-" * 65)
    print(" [STATUS] COMPACTION CRASH SAFETY VERIFIED SUCCESSFULLY")
    print("-" * 65)

if __name__ == "__main__":
    run_compaction_crash_tests()