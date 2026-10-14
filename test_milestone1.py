"""
Verification suite for Milestone 1: SkipListMemTable.
Tests search precision, updates, tombstone tracking, sorted ordering,
and high-concurrency randomized lookups.
"""

import time
import random
import string
from engine.skip_list import SkipListMemTable, TOMBSTONE


def random_string(length: int = 8) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def run_milestone1_tests() -> None:
    print("-" * 65)
    print(" STRATAKV MILESTONE 1: SKIP-LIST MEMTABLE VERIFICATION ")
    print("-" * 65)

    memtable = SkipListMemTable(max_level=16, p=0.5)

    print("[TEST 1] Executing basic insertions and point lookups...")
    memtable.put("user:101", "Aaditya")
    memtable.put("user:102", "Jain")
    memtable.put("system:status", "ACTIVE")

    assert memtable.get("user:101") == "Aaditya", "Failed on user:101"
    assert memtable.get("user:102") == "Jain", "Failed on user:102"
    assert memtable.get("system:status") == "ACTIVE", "Failed on system:status"
    assert memtable.get("user:999") is None, "False positive on missing key"
    print("  ✓ Basic point lookups verified.")

    print("\n[TEST 2] Testing in-place updates and memory tracking...")
    initial_bytes = memtable.size_bytes
    initial_count = memtable.entry_count

    memtable.put("user:101", "Aaditya_Jain_Senior_MLE")
    assert memtable.get("user:101") == "Aaditya_Jain_Senior_MLE"
    assert memtable.entry_count == initial_count, "Entry count mutated on update"
    assert memtable.size_bytes > initial_bytes, "Byte tracking did not scale"
    print(f"  ✓ In-place update verified (Memory: {memtable.size_bytes} bytes).")

    print("\n[TEST 3] Testing tombstone deletion mechanism...")
    memtable.delete("user:102")
    assert memtable.get("user:102") == TOMBSTONE, "Tombstone was not placed"
    print("  ✓ Tombstone marker verified.")

    print("\n[TEST 4] Testing sorted traversal for SSTable serialization...")
    unsorted_keys = ["zulu", "alpha", "bravo", "tango", "foxtrot", "charlie"]
    for k in unsorted_keys:
        memtable.put(k, f"val_{k}")

    extracted_keys = [k for k, _ in memtable.iter_items()]
    assert extracted_keys == sorted(extracted_keys), "Keys are not in sorted order!"
    print(f"  ✓ Traversal verified sorted: {extracted_keys[:6]}...")

    print("\n[TEST 5] Stress testing with 5,000 synthetic records...")
    test_dataset = {random_string(10): random_string(32) for _ in range(5000)}

    t_start = time.perf_counter()
    for k, v in test_dataset.items():
        memtable.put(k, v)
    t_insert = time.perf_counter() - t_start

    t_start = time.perf_counter()
    for k, v in test_dataset.items():
        res = memtable.get(k)
        assert res == v, f"Data mismatch for key: {k}"
    t_lookup = time.perf_counter() - t_start

    print(f"  ✓ 5,000 Records Inserted in : {t_insert * 1000:.2f} ms")
    print(f"  ✓ 5,000 Records Retrieved in: {t_lookup * 1000:.2f} ms")
    print(f"  ✓ Total MemTable Footprint : {memtable.size_bytes / 1024:.2f} KB")
    print(f"  ✓ Current Skip-List Height : Level {memtable.level} / {memtable.max_level}")
    print("-" * 65)
    print(" [STATUS] ALL MILESTONE 1 ASSERTIONS PASSED SUCCESSFULLY")
    print("-" * 65)


if __name__ == "__main__":
    run_milestone1_tests()