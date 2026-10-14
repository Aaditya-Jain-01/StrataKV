import os
from engine.skip_list import SkipListMemTable, TOMBSTONE
from engine.wal import WriteAheadLog

WAL_PATH = "test_wal.log"

def run_milestone2_tests() -> None:
    print("-" * 65)
    print(" STRATAKV MILESTONE 2: CRASH-RESILIENT WAL VERIFICATION ")
    print("-" * 65)

    if os.path.exists(WAL_PATH):
        os.remove(WAL_PATH)

    wal = WriteAheadLog(WAL_PATH)
    print("[TEST 1] Logging operations to binary WAL...")
    wal.append("alpha", "100")
    wal.append("bravo", "200")
    wal.append("charlie", "300")
    wal.append("alpha", "999")
    wal.append("bravo", TOMBSTONE)
    wal.close()
    print("  Append operations written and flushed.")

    print("\n[TEST 2] Simulating crash and recovering into new MemTable...")
    recovered_memtable = SkipListMemTable()
    wal_recovery = WriteAheadLog(WAL_PATH)
    for key, val in wal_recovery.recover():
        recovered_memtable.put(key, val)
    wal_recovery.close()

    assert recovered_memtable.get("alpha") == "999", "Failed on alpha recovery"
    assert recovered_memtable.get("bravo") == TOMBSTONE, "Failed on tombstone recovery"
    assert recovered_memtable.get("charlie") == "300", "Failed on charlie recovery"
    assert recovered_memtable.get("delta") is None, "False recovery on absent key"
    print("  State recovery verified.")

    print("\n[TEST 3] Simulating corrupted disk write (torn-write checksum test)...")
    with open(WAL_PATH, "r+b") as f:
        f.seek(0, os.SEEK_END)
        f.write(b"GARBAGE_TRAILING_CORRUPT_BYTES_XYZ_123456789")

    corrupt_recovery_wal = WriteAheadLog(WAL_PATH)
    recovered_records = list(corrupt_recovery_wal.recover())
    assert len(recovered_records) == 5, f"Expected 5 valid records, got {len(recovered_records)}"
    print("  Torn-write corruption halted cleanly via CRC32.")
    print("  Corrupt suffix truncated to last valid offset.")

    print("\n[TEST 4] Appending new writes after recovery & verifying multi-restart durability...")
    corrupt_recovery_wal.append("delta", "400")
    corrupt_recovery_wal.append("echo", "500")
    corrupt_recovery_wal.close()

    second_restart_wal = WriteAheadLog(WAL_PATH)
    all_reconstructed = list(second_restart_wal.recover())
    second_restart_wal.close()

    assert len(all_reconstructed) == 7, f"Expected 7 records after restart, got {len(all_reconstructed)}"
    assert all_reconstructed[-2] == ("delta", "400"), "Post-corruption write 'delta' lost"
    assert all_reconstructed[-1] == ("echo", "500"), "Post-corruption write 'echo' lost"
    print("  Writes appended post-recovery are durable across subsequent restarts.")

    print("\n[TEST 5] Testing WAL reset on flush...")
    wal_reset = WriteAheadLog(WAL_PATH)
    wal_reset.clear()
    assert len(list(wal_reset.recover())) == 0, "WAL not empty after clear"
    wal_reset.close()
    print("  WAL truncate verified.")

    if os.path.exists(WAL_PATH):
        os.remove(WAL_PATH)

    print("-" * 65)
    print(" [STATUS] ALL MILESTONE 2 ASSERTIONS PASSED SUCCESSFULLY")
    print("-" * 65)

if __name__ == "__main__":
    run_milestone2_tests()