import struct
import pytest
from engine.bloom_filter import BloomFilter, CorruptedBloomFilterError


def run_bloom_integrity_tests() -> None:
    print("-" * 65)
    print(" STRATAKV DATA INTEGRITY: BLOOM FILTER CORRUPTION VALIDATION ")
    print("-" * 65)

    print("[TEST 1] Testing normal serialization and roundtrip reconstruction...")
    bf = BloomFilter(expected_elements=500, false_positive_rate=0.01)
    for i in range(200):
        bf.add(f"test_key_{i}")

    data = bf.to_bytes()
    reconstructed = BloomFilter.from_bytes(data)
    for i in range(200):
        assert reconstructed.contains(f"test_key_{i}") is True
    assert reconstructed.contains("missing_key_xyz") is False
    print("  Roundtrip serialization and lookup verified.")

    print("\n[TEST 2] Rejecting empty and truncated headers (< 8 bytes)...")
    for bad_bytes in [b"", b"\x00", b"\x00\x01\x02\x03\x04\x05\x06"]:
        try:
            BloomFilter.from_bytes(bad_bytes)
            assert False, f"Accepted undersized header: {bad_bytes}"
        except CorruptedBloomFilterError:
            pass
    print("  Truncated headers successfully rejected via CorruptedBloomFilterError.")

    print("\n[TEST 3] Rejecting invalid parameter bounds (num_bits and num_hashes)...")
    bad_params = [
        (0, 5),          # num_bits = 0
        (4, 5),          # num_bits < 8
        (1000, 0),       # num_hashes = 0
        (1000, 1000),    # num_hashes > MAX_HASHES
        (200000000, 5),  # num_bits > MAX_BITS
    ]
    for n_bits, n_hashes in bad_params:
        bad_header = struct.pack("!II", n_bits, n_hashes) + b"\x00" * ((max(n_bits, 8) + 7) // 8)
        try:
            BloomFilter.from_bytes(bad_header)
            assert False, f"Accepted invalid params: num_bits={n_bits}, num_hashes={n_hashes}"
        except CorruptedBloomFilterError:
            pass
    print("  Out-of-bound configuration parameters rejected.")

    print("\n[TEST 4] Rejecting truncated payload data...")
    header = struct.pack("!II", 64, 4)  # 64 bits = 8 bytes expected
    truncated_payload = header + b"\x00" * 5  # Only 5 bytes instead of 8
    try:
        BloomFilter.from_bytes(truncated_payload)
        assert False, "Accepted truncated bit array payload"
    except CorruptedBloomFilterError:
        print("  Truncated payload rejected before runtime indexing error.")

    print("\n[TEST 5] Rejecting trailing garbage bytes...")
    oversize_payload = header + b"\x00" * 8 + b"TRAILING_GARBAGE_BYTES"
    try:
        BloomFilter.from_bytes(oversize_payload)
        assert False, "Accepted payload with trailing unparsed garbage"
    except CorruptedBloomFilterError:
        print("  Trailing garbage bytes rejected.")

    print("-" * 65)
    print(" [STATUS] BLOOM FILTER CORRUPTION VALIDATION PASSED SUCCESSFULLY")
    print("-" * 65)


if __name__ == "__main__":
    run_bloom_integrity_tests()