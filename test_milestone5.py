import os
import shutil
from engine.lsm_tree import LSMTree

TEST_DIR = "test_lsm_store"

def cleanup():
    if os.path.exists(TEST_DIR):
        shutil.rmtree(TEST_DIR)

def run_milestone5_tests() -> None:
    print("-" * 65)
    print(" STRATAKV MILESTONE 5: INTEGRATED LSM-TREE VERIFICATION ")
    print("-" * 65)

    cleanup()

    print("[TEST 1] Testing unified write, read, and delete operations...")
    tree = LSMTree(data_dir=TEST_DIR, memtable_threshold_bytes=1024)
    tree.put("session:1", "active")
    tree.put("session:2", "idle")
    assert tree.get("session:1") == "active"
    assert tree.get("session:2") == "idle"

    tree.delete("session:2")
    assert tree.get("session:2") is None
    assert tree.get("session:99") is None
    print("  Basic point read/write/delete lifecycle verified.")

    print("\n[TEST 2] Testing automated MemTable flush to SSTable...")
    for i in range(50):
        tree.put(f"bulk_key_{i:03d}", f"bulk_val_{i:03d}_{'x'*30}")

    assert len(tree.sstables) >= 1, "MemTable threshold did not trigger automated disk flush"
    assert tree.get("bulk_key_025") == f"bulk_val_025_{'x'*30}"
    assert tree.get("session:1") == "active"
    print(f"  Automated flush verified ({len(tree.sstables)} SSTable(s) generated on disk).")

    print("\n[TEST 3] Testing crash recovery from WAL and existing SSTables...")
    tree.put("unflushed_key_1", "survives_crash")
    tree.close()

    recovered_tree = LSMTree(data_dir=TEST_DIR, memtable_threshold_bytes=1024)
    assert len(recovered_tree.sstables) >= 1, "SSTables failed to reload on restart"
    assert recovered_tree.get("unflushed_key_1") == "survives_crash", "Unflushed WAL record lost on restart"
    assert recovered_tree.get("bulk_key_000") is not None, "Historical SSTable record unavailable"
    print("  Complete engine restart and WAL recovery verified.")

    print("\n[TEST 4] Testing leveled multi-SSTable compaction...")
    for i in range(100):
        recovered_tree.put(f"key_stream_{i:03d}", f"data_{i}")
        if i % 25 == 0:
            recovered_tree.flush()

    for i in range(0, 50, 2):
        recovered_tree.delete(f"key_stream_{i:03d}")

    pre_compact_tables = len(recovered_tree.sstables)
    assert pre_compact_tables >= 2, "Expected multiple SSTables before compaction"
    print(f"  Pre-compaction SSTable count: {pre_compact_tables}")

    recovered_tree.compact()
    post_compact_tables = len(recovered_tree.sstables)
    print(f"  Post-compaction SSTable count: {post_compact_tables}")
    assert post_compact_tables == 1, f"Expected 1 compacted table, got {post_compact_tables}"

    assert recovered_tree.get("key_stream_001") == "data_1"
    assert recovered_tree.get("key_stream_000") is None
    assert recovered_tree.get("key_stream_002") is None
    print("  K-way merge compaction and tombstone eviction verified.")

    recovered_tree.close()
    cleanup()

    print("-" * 65)
    print(" [STATUS] ALL MILESTONE 5 ASSERTIONS PASSED SUCCESSFULLY")
    print("-" * 65)

if __name__ == "__main__":
    run_milestone5_tests()