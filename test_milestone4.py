import random
import string
from engine.bloom_filter import BloomFilter

def random_string(length: int = 12) -> str:
    return "".join(random.choices(string.ascii_letters + string.digits, k=length))

def run_milestone4_tests() -> None:
    print("-" * 65)
    print(" STRATAKV MILESTONE 4: BITWISE BLOOM FILTER VERIFICATION ")
    print("-" * 65)

    print("[TEST 1] Testing exact set membership (Zero False Negatives)...")
    bf = BloomFilter(expected_elements=1000, false_positive_rate=0.01)
    keys = [f"inserted_key_{i}" for i in range(1000)]
    for k in keys:
        bf.add(k)

    for k in keys:
        assert bf.contains(k) is True, f"False negative encountered for key: {k}"
    print(f"  All {len(keys)} inserted keys confirmed present.")

    print("\n[TEST 2] Measuring empirical false positive rate across 10,000 queries...")
    absent_keys = [f"absent_query_{i}" for i in range(10000)]
    false_positives = sum(1 for k in absent_keys if bf.contains(k))
    empirical_fpr = (false_positives / len(absent_keys)) * 100.0

    print(f"  Theoretical Target FPR: {bf.p * 100.0:.2f}%")
    print(f"  Empirical FPR         : {empirical_fpr:.2f}% ({false_positives}/{len(absent_keys)} false positives)")
    assert empirical_fpr <= 2.0, f"Empirical FPR {empirical_fpr}% exceeded safety ceiling"
    print("  Empirical false positive rate bounded within acceptable threshold.")

    print("\n[TEST 3] Bit array sizing and hash count verification...")
    print(f"  Allocated Bits (m)    : {bf.num_bits} bits ({bf.num_bytes} bytes)")
    print(f"  Hash Functions (k)    : {bf.num_hashes}")
    assert bf.num_hashes >= 4, f"Unexpectedly low hash function count: {bf.num_hashes}"
    print("  Mathematical parameter bounds confirmed.")

    print("\n[TEST 4] Binary serialization and reconstruction...")
    serialized = bf.to_bytes()
    reconstructed_bf = BloomFilter.from_bytes(serialized)

    for k in keys[:100]:
        assert reconstructed_bf.contains(k) is True, "Reconstructed filter failed on valid key"
    assert reconstructed_bf.contains("totally_absent_random_token_xyz") is False
    print("  Binary serialization to/from bytes verified.")

    print("-" * 65)
    print(" [STATUS] ALL MILESTONE 4 ASSERTIONS PASSED SUCCESSFULLY")
    print("-" * 65)

if __name__ == "__main__":
    run_milestone4_tests()