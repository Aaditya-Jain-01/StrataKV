import os
import shutil
import glob
from engine.lsm_tree import LSMTree

TEST_DIR = "test_flush_crash_store"

def cleanup():
    if os.path.exists(TEST_DIR):
        shutil.rmtree(TEST_DIR, ignore_errors=True)

def run_flush_crash_tests():
    cleanup()
    print("-" * 65)
    print(" STRATAKV FAULT INJECTION: CRASH-SAFE FLUSH PROTOCOL ")
    print("-" * 65)

    print("[TEST 1] Populating MemTable with active uncommitted records...")
    tree = LSMTree(data_dir=TEST_DIR, memtable_threshold_bytes=64 * 1024)
    for i in range(100):
        tree.put(f"order:{i:04d}", f"details_{i}")

    assert tree.memtable.entry_count == 100
    assert len(tree.sstables) == 0
    print("  100 records buffered in MemTable and WAL.")

    print("\n[TEST 2] Simulating crash during flush (after staging files, before manifest)...")
    original_save_manifest = tree._save_manifest

    def crash_before_manifest(table_bases):
        raise RuntimeError("SIMULATED_POWER_CUT_BEFORE_MANIFEST_COMMIT")

    tree._save_manifest = crash_before_manifest
    try:
        tree.flush()
    except RuntimeError:
        pass
    finally:
        tree._save_manifest = original_save_manifest

    tree.close()

    raw_sst_files = glob.glob(os.path.join(TEST_DIR, "sst_*"))
    print(f"  Physical uncommitted SST files on disk before restart: {len(raw_sst_files)}")
    assert len(raw_sst_files) > 0

    print("\n[TEST 3] Restarting engine; asserting uncommitted SSTables swept & WAL replayed...")
    recovered_tree = LSMTree(data_dir=TEST_DIR, memtable_threshold_bytes=64 * 1024)

    assert len(recovered_tree.sstables) == 0, "Uncommitted SSTable was incorrectly registered"
    assert recovered_tree.memtable.entry_count == 100, "WAL records failed to replay into MemTable"

    for i in range(100):
        assert recovered_tree.get(f"order:{i:04d}") == f"details_{i}"
    print("  Zero data loss: 100% of writes successfully recovered from WAL.")

    surviving_sst_files = glob.glob(os.path.join(TEST_DIR, "sst_*"))
    assert len(surviving_sst_files) == 0, "Orphaned uncommitted SSTable files were not swept"
    print("  Orphaned uncommitted flush files swept cleanly from disk.")

    print("\n[TEST 4] Executing clean flush and verifying normal lifecycle...")
    flushed_name = recovered_tree.flush()
    assert flushed_name is not None
    assert len(recovered_tree.sstables) == 1
    assert recovered_tree.memtable.entry_count == 0

    for i in range(100):
        assert recovered_tree.get(f"order:{i:04d}") == f"details_{i}"
    print("  Clean flush executed successfully; data readable from durable SSTable.")

    recovered_tree.close()
    cleanup()
    print("-" * 65)
    print(" [STATUS] FLUSH CRASH SAFETY VERIFIED SUCCESSFULLY")
    print("-" * 65)

if __name__ == "__main__":
    run_flush_crash_tests()