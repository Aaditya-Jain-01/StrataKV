import os
import shutil
import random
import string
from engine.lsm_tree import LSMTree

TEST_DIR = "test_invariants_store"

def cleanup():
    if os.path.exists(TEST_DIR):
        shutil.rmtree(TEST_DIR, ignore_errors=True)

def random_val(length: int = 32) -> str:
    return "".join(random.choices(string.ascii_letters + string.digits, k=length))

def run_invariant_tests():
    cleanup()
    print("-" * 65)
    print(" STRATAKV INVARIANT VERIFICATION: MULTI-CYCLE DIFFERENTIAL ORACLE ")
    print("-" * 65)

    random.seed(42)  # Deterministic seed for reproducible verification
    oracle = {}
    key_pool = [f"account:{i:03d}" for i in range(120)]

    print("[STAGE 1] Executing 4 generations of churn, flushes, and restarts...")
    
    # Generation 1: Initial population (sparse distribution)
    tree = LSMTree(data_dir=TEST_DIR, memtable_threshold_bytes=4 * 1024)
    for k in key_pool[:80]:
        v = random_val()
        oracle[k] = v
        tree.put(k, v)
    tree.flush()
    tree.close()
    print("  Generation 1: 80 keys populated and flushed.")

    # Generation 2: Overwrites, fresh keys, and crash restart
    tree = LSMTree(data_dir=TEST_DIR, memtable_threshold_bytes=4 * 1024)
    for k in key_pool[:40]:  # Overwrite first 40 keys
        v = f"updated_{random_val()}"
        oracle[k] = v
        tree.put(k, v)

    for k in key_pool[80:]:  # Insert remaining 40 keys
        v = random_val()
        oracle[k] = v
        tree.put(k, v)

    for k in key_pool[10:30]:  # Tombstone 20 keys
        oracle.pop(k, None)
        tree.delete(k)

    tree.flush()
    tree.close()
    print("  Generation 2: 40 overwrites, 40 new inserts, 20 deletions flushed.")

    # Generation 3: Intermediate Compaction with uncommitted WAL writes
    tree = LSMTree(data_dir=TEST_DIR, memtable_threshold_bytes=4 * 1024)
    tree.compact()
    assert len(tree.sstables) == 1, f"Expected 1 compacted table, got {len(tree.sstables)}"

    for k in key_pool[10:20]:  # Revive 10 previously deleted keys
        v = f"revived_{random_val()}"
        oracle[k] = v
        tree.put(k, v)

    for k in key_pool[60:80]:  # Delete another 20 keys
        oracle.pop(k, None)
        tree.delete(k)

    # Simulate abrupt process kill without flush (relying on WAL recovery)
    tree.close()
    print("  Generation 3: Compaction executed, key revivals and deletes left in WAL.")

    # Generation 4: Recovery, differential verification, second compaction
    print("\n[STAGE 2] Re-opening engine from crash and asserting ground-truth...")
    tree = LSMTree(data_dir=TEST_DIR, memtable_threshold_bytes=4 * 1024)

    # Verify every key in oracle matches engine state
    for k, expected_v in oracle.items():
        actual_v = tree.get(k)
        assert actual_v == expected_v, f"Invariant mismatch for key '{k}': expected '{expected_v}', got '{actual_v}'"

    # Verify deleted/missing keys return None (no tombstone resurrection)
    all_deleted = (set(key_pool[20:30]) | set(key_pool[60:80]))
    for k in all_deleted:
        assert tree.get(k) is None, f"Deleted key '{k}' resurrected or returned non-None"

    # Verify completely absent keys return None
    for i in range(200, 250):
        assert tree.get(f"account:{i:03d}") is None, f"Ghost hit on non-existent key 'account:{i:03d}'"
    print("  100% of oracle key-value invariants verified across crash boundary.")

    print("\n[STAGE 3] Final compaction and post-purge differential assertion...")
    tree.flush()
    tree.compact()
    assert len(tree.sstables) == 1

    for k, expected_v in oracle.items():
        assert tree.get(k) == expected_v, f"Post-compaction drift on key '{k}'"

    for k in all_deleted:
        assert tree.get(k) is None, f"Post-compaction resurrection on key '{k}'"

    print("  Post-compaction differential integrity verified.")

    tree.close()
    cleanup()
    print("-" * 65)
    print(" [STATUS] ALL DATA INVARIANTS & RECOVERY CYCLES VERIFIED SUCCESSFULLY")
    print("-" * 65)

if __name__ == "__main__":
    run_invariant_tests()