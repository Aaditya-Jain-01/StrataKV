import os
import struct
import pytest
from engine.sstable import SSTableWriter, SSTableReader, CorruptedSSTableError

SST_PATH = "test_corrupt.sst"
IDX_PATH = "test_corrupt.idx"

def cleanup():
    for p in (SST_PATH, IDX_PATH):
        if os.path.exists(p):
            os.remove(p)

def run_sstable_integrity_tests():
    cleanup()
    print("-" * 65)
    print(" STRATAKV DATA INTEGRITY: SSTABLE CORRUPTION DETECTION ")
    print("-" * 65)

    print("[TEST 1] Writing baseline dataset...")
    items = [(f"k_{i:04d}", f"val_{i}") for i in range(100)]
    SSTableWriter.write(SST_PATH, IDX_PATH, iter(items), sparse_interval=10)
    reader = SSTableReader(SST_PATH, IDX_PATH)
    assert reader.get("k_0050") == "val_50"
    print("  Baseline read verified.")

    print("\n[TEST 2] Detecting truncated record header...")
    with open(SST_PATH, "ab") as f:
        f.write(b"\x00\x01\x02")  # Write 3 bytes (HEADER_SIZE is 12 bytes)

    r2 = SSTableReader(SST_PATH, IDX_PATH)
    try:
        list(r2.scan())
        assert False, "Reader failed to raise on truncated header"
    except CorruptedSSTableError:
        print("  Truncated record header caught via CorruptedSSTableError.")

    print("\n[TEST 3] Detecting truncated record payload...")
    cleanup()
    SSTableWriter.write(SST_PATH, IDX_PATH, iter(items), sparse_interval=10)
    sst_len = os.path.getsize(SST_PATH)
    with open(SST_PATH, "r+b") as f:
        f.seek(sst_len - 4)
        f.truncate()  # Cut off last 4 bytes of the final record payload

    r3 = SSTableReader(SST_PATH, IDX_PATH)
    try:
        list(r3.scan())
        assert False, "Reader failed to raise on truncated payload"
    except CorruptedSSTableError:
        print("  Truncated record payload caught via CorruptedSSTableError.")

    print("\n[TEST 4] Detecting silent bit rot (CRC32 mismatch)...")
    cleanup()
    SSTableWriter.write(SST_PATH, IDX_PATH, iter(items), sparse_interval=10)
    with open(SST_PATH, "r+b") as f:
        f.seek(25)  # Seek directly into a data payload
        byte = f.read(1)
        f.seek(25)
        f.write(bytes([byte[0] ^ 0xFF]))  # Flip bits

    r4 = SSTableReader(SST_PATH, IDX_PATH)
    try:
        list(r4.scan())
        assert False, "Reader failed to detect CRC32 mismatch"
    except CorruptedSSTableError:
        print("  Bit-rot corruption caught deterministically via CRC32.")

    print("\n[TEST 5] Detecting malformed index with out-of-order keys...")
    cleanup()
    with open(SST_PATH, "wb") as f:
        f.write(b"dummy")
    with open(IDX_PATH, "wb") as f:
        k1, k2 = b"zebra", b"apple"
        f.write(struct.pack("!IQ", len(k1), 0) + k1)
        f.write(struct.pack("!IQ", len(k2), 10) + k2)

    try:
        SSTableReader(SST_PATH, IDX_PATH)
        assert False, "Reader accepted unsorted index keys"
    except CorruptedSSTableError:
        print("  Unsorted index keys rejected at load time.")

    print("\n[TEST 6] Detecting index offset out of file bounds...")
    cleanup()
    with open(SST_PATH, "wb") as f:
        f.write(b"short_file")  # 10 bytes
    with open(IDX_PATH, "wb") as f:
        k = b"alpha"
        f.write(struct.pack("!IQ", len(k), 999999) + k)

    try:
        SSTableReader(SST_PATH, IDX_PATH)
        assert False, "Reader accepted out-of-bounds index offset"
    except CorruptedSSTableError:
        print("  Out-of-bounds index offset rejected at load time.")

    cleanup()
    print("-" * 65)
    print(" [STATUS] SSTABLE CORRUPTION DETECTION VERIFIED SUCCESSFULLY")
    print("-" * 65)

if __name__ == "__main__":
    run_sstable_integrity_tests()