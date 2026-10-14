import os
from engine.skip_list import SkipListMemTable, TOMBSTONE
from engine.sstable import SSTableWriter, SSTableReader

SST_PATH = "test_data.sst"
IDX_PATH = "test_data.idx"

def cleanup():
    for p in (SST_PATH, IDX_PATH):
        if os.path.exists(p):
            os.remove(p)

def run_milestone3_tests() -> None:
    print("-" * 65)
    print(" STRATAKV MILESTONE 3: SSTABLE & SPARSE INDEX VERIFICATION ")
    print("-" * 65)

    cleanup()

    print("[TEST 1] Populating MemTable and serializing to SSTable...")
    memtable = SkipListMemTable()
    for i in range(100):
        key = f"key_{i:04d}"
        val = f"value_payload_{i}"
        memtable.put(key, val)
    memtable.put("key_0042", TOMBSTONE)

    written_count = SSTableWriter.write(
        SST_PATH, IDX_PATH, memtable.iter_items(), sparse_interval=10
    )
    assert written_count == 100, f"Expected 100 records, got {written_count}"
    print(f"  Flushed {written_count} sorted entries to disk.")

    print("\n[TEST 2] Verifying sparse index cardinality...")
    reader = SSTableReader(SST_PATH, IDX_PATH)
    assert len(reader.sparse_index) == 10, f"Expected 10 index offsets, got {len(reader.sparse_index)}"
    print(f"  Sparse index loaded with {len(reader.sparse_index)} checkpoints.")

    print("\n[TEST 3] Point lookups via bounded sparse index seek...")
    assert reader.get("key_0000") == "value_payload_0", "Failed on lower boundary key_0000"
    assert reader.get("key_0055") == "value_payload_55", "Failed on interior key_0055"
    assert reader.get("key_0099") == "value_payload_99", "Failed on upper boundary key_0099"
    assert reader.get("key_0042") == TOMBSTONE, "Failed on tombstone retrieval"
    print("  Bounded disk lookups verified.")

    print("\n[TEST 4] Missing-key boundary assertions...")
    assert reader.get("key_0000_absent") is None, "False positive on absent interior key"
    assert reader.get("a_before_min") is None, "False positive on pre-range key"
    assert reader.get("z_after_max") is None, "False positive on post-range key"
    print("  Missing-key checks verified.")

    print("\n[TEST 5] Full sequential table scan...")
    scanned_items = list(reader.scan())
    assert len(scanned_items) == 100, f"Expected 100 scanned items, got {len(scanned_items)}"
    assert scanned_items[0][0] == "key_0000"
    assert scanned_items[-1][0] == "key_0099"
    print(f"  Scanned all {len(scanned_items)} records in lexicographical order.")

    cleanup()

    print("-" * 65)
    print(" [STATUS] ALL MILESTONE 3 ASSERTIONS PASSED SUCCESSFULLY")
    print("-" * 65)

if __name__ == "__main__":
    run_milestone3_tests()