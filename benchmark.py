import os
import sys
import json
import time
import shutil
import sqlite3
import platform
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from engine.lsm_tree import LSMTree

BENCH_DIR_LSM = "bench_lsm_store"
BENCH_SQLITE_FILE = "bench_sqlite.db"
NUM_OPERATIONS = 10000
REPETITIONS = 3


def cleanup() -> None:
    if os.path.exists(BENCH_DIR_LSM):
        shutil.rmtree(BENCH_DIR_LSM, ignore_errors=True)
    if os.path.exists(BENCH_SQLITE_FILE):
        try:
            os.remove(BENCH_SQLITE_FILE)
        except OSError:
            pass


def calculate_metrics(latencies_sec: list[float]) -> dict[str, float]:
    arr = np.array(latencies_sec) * 1000.0  # Convert to ms
    return {
        "p50_ms": float(np.percentile(arr, 50)),
        "p95_ms": float(np.percentile(arr, 95)),
        "p99_ms": float(np.percentile(arr, 99)),
        "mean_ms": float(np.mean(arr)),
        "std_ms": float(np.std(arr)),
    }


def run_benchmark() -> None:
    cleanup()
    print("-" * 70)
    print(f" STRATAKV VS SQLITE: SCIENTIFIC BENCHMARK ({REPETITIONS} RUNS x {NUM_OPERATIONS:,} OPS) ")
    print("-" * 70)

    keys = [f"usr_{i:06d}" for i in range(NUM_OPERATIONS)]
    values = [f"payload_value_blob_{i:06d}_{'a'*24}" for i in range(NUM_OPERATIONS)]
    absent_keys = [f"nonexistent_key_{i:06d}" for i in range(NUM_OPERATIONS)]
    expected_kv = dict(zip(keys, values))

    raw_results = {
        "metadata": {
            "platform": platform.platform(),
            "processor": platform.processor(),
            "python_version": sys.version.split()[0],
            "sqlite_version": sqlite3.sqlite_version,
            "numpy_version": np.__version__,
            "matplotlib_version": matplotlib.__version__,
            "num_operations": NUM_OPERATIONS,
            "repetitions": REPETITIONS,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        },
        "stratakv": {"writes": [], "reads": [], "missing": []},
        "sqlite": {"writes": [], "reads": [], "missing": []},
    }

    last_stratakv_sample_latencies: list[float] = []
    last_sqlite_sample_latencies: list[float] = []

    for run_idx in range(1, REPETITIONS + 1):
        print(f"\n[ITERATION {run_idx}/{REPETITIONS}] Executing benchmarks...")
        cleanup()

        # ----------------- StrataKV -----------------
        lsm = LSMTree(data_dir=BENCH_DIR_LSM, memtable_threshold_bytes=64 * 1024)

        # 1. Writes
        t0 = time.perf_counter()
        w_lats = []
        for k, v in zip(keys, values):
            t_op = time.perf_counter()
            lsm.put(k, v)
            w_lats.append(time.perf_counter() - t_op)
        w_total = time.perf_counter() - t0
        w_ops = NUM_OPERATIONS / w_total

        # Flush MemTable to disk before reading
        lsm.flush()

        # 2. Existing Reads with Invariant Assertions
        t0 = time.perf_counter()
        r_lats = []
        for k in keys:
            t_op = time.perf_counter()
            res = lsm.get(k)
            r_lats.append(time.perf_counter() - t_op)
            if res != expected_kv[k]:
                raise AssertionError(f"Correctness failure in StrataKV on key '{k}'")
        r_total = time.perf_counter() - t0
        r_ops = NUM_OPERATIONS / r_total

        # 3. Missing-Key Reads
        t0 = time.perf_counter()
        m_lats = []
        for k in absent_keys:
            t_op = time.perf_counter()
            res = lsm.get(k)
            m_lats.append(time.perf_counter() - t_op)
            if res is not None:
                raise AssertionError(f"Correctness failure: non-existent key '{k}' returned non-None")
        m_total = time.perf_counter() - t0
        m_ops = NUM_OPERATIONS / m_total

        lsm.close()

        raw_results["stratakv"]["writes"].append({"ops_sec": w_ops, "metrics": calculate_metrics(w_lats)})
        raw_results["stratakv"]["reads"].append({"ops_sec": r_ops, "metrics": calculate_metrics(r_lats)})
        raw_results["stratakv"]["missing"].append({"ops_sec": m_ops, "metrics": calculate_metrics(m_lats)})

        if run_idx == REPETITIONS:
            last_stratakv_sample_latencies = w_lats

        # ----------------- SQLite Baseline -----------------
        conn = sqlite3.connect(BENCH_SQLITE_FILE)
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT)")
        conn.commit()

        # 1. Writes
        t0 = time.perf_counter()
        sql_w_lats = []
        for k, v in zip(keys, values):
            t_op = time.perf_counter()
            cursor.execute("INSERT OR REPLACE INTO kv VALUES (?, ?)", (k, v))
            conn.commit()
            sql_w_lats.append(time.perf_counter() - t_op)
        sql_w_total = time.perf_counter() - t0
        sql_w_ops = NUM_OPERATIONS / sql_w_total

        # 2. Existing Reads with Invariant Assertions
        t0 = time.perf_counter()
        sql_r_lats = []
        for k in keys:
            t_op = time.perf_counter()
            cursor.execute("SELECT value FROM kv WHERE key = ?", (k,))
            row = cursor.fetchone()
            sql_r_lats.append(time.perf_counter() - t_op)
            if row is None or row[0] != expected_kv[k]:
                raise AssertionError(f"Correctness failure in SQLite on key '{k}'")
        sql_r_total = time.perf_counter() - t0
        sql_r_ops = NUM_OPERATIONS / sql_r_total

        # 3. Missing-Key Reads
        t0 = time.perf_counter()
        sql_m_lats = []
        for k in absent_keys:
            t_op = time.perf_counter()
            cursor.execute("SELECT value FROM kv WHERE key = ?", (k,))
            row = cursor.fetchone()
            sql_m_lats.append(time.perf_counter() - t_op)
            if row is not None:
                raise AssertionError(f"Correctness failure: SQLite returned row for absent key '{k}'")
        sql_m_total = time.perf_counter() - t0
        sql_m_ops = NUM_OPERATIONS / sql_m_total

        conn.close()

        raw_results["sqlite"]["writes"].append({"ops_sec": sql_w_ops, "metrics": calculate_metrics(sql_w_lats)})
        raw_results["sqlite"]["reads"].append({"ops_sec": sql_r_ops, "metrics": calculate_metrics(sql_r_lats)})
        raw_results["sqlite"]["missing"].append({"ops_sec": sql_m_ops, "metrics": calculate_metrics(sql_m_lats)})

        if run_idx == REPETITIONS:
            last_sqlite_sample_latencies = sql_w_lats

    # Compute aggregate medians across independent repetitions
    med_skv_w_ops = float(np.median([r["ops_sec"] for r in raw_results["stratakv"]["writes"]]))
    med_skv_r_ops = float(np.median([r["ops_sec"] for r in raw_results["stratakv"]["reads"]]))
    med_skv_m_ops = float(np.median([r["ops_sec"] for r in raw_results["stratakv"]["missing"]]))

    med_sql_w_ops = float(np.median([r["ops_sec"] for r in raw_results["sqlite"]["writes"]]))
    med_sql_r_ops = float(np.median([r["ops_sec"] for r in raw_results["sqlite"]["reads"]]))
    med_sql_m_ops = float(np.median([r["ops_sec"] for r in raw_results["sqlite"]["missing"]]))

    med_skv_w_p99 = float(np.median([r["metrics"]["p99_ms"] for r in raw_results["stratakv"]["writes"]]))
    med_skv_r_p99 = float(np.median([r["metrics"]["p99_ms"] for r in raw_results["stratakv"]["reads"]]))
    med_skv_m_p99 = float(np.median([r["metrics"]["p99_ms"] for r in raw_results["stratakv"]["missing"]]))

    med_sql_w_p99 = float(np.median([r["metrics"]["p99_ms"] for r in raw_results["sqlite"]["writes"]]))
    med_sql_r_p99 = float(np.median([r["metrics"]["p99_ms"] for r in raw_results["sqlite"]["reads"]]))
    med_sql_m_p99 = float(np.median([r["metrics"]["p99_ms"] for r in raw_results["sqlite"]["missing"]]))

    print("\n" + "=" * 70)
    print(" EMPIRICAL PERFORMANCE RESULTS SUMMARY (MEDIAN ACROSS RUNS) ")
    print("=" * 70)
    print(f"{'Metric':<25} | {'StrataKV':<14} | {'SQLite':<14} | {'Gain / Variance':<14}")
    print("-" * 70)
    print(f"{'Write Ops/sec':<25} | {med_skv_w_ops:<14.2f} | {med_sql_w_ops:<14.2f} | {med_skv_w_ops/med_sql_w_ops:.2f}x Throughput")
    print(f"{'Write P99 Latency (ms)':<25} | {med_skv_w_p99:<14.3f} | {med_sql_w_p99:<14.3f} | {med_sql_w_p99/med_skv_w_p99:.2f}x Lower Latency")
    print(f"{'Read Ops/sec (Hits)':<25} | {med_skv_r_ops:<14.2f} | {med_sql_r_ops:<14.2f} | {med_skv_r_ops/med_sql_r_ops:.2f}x Variance")
    print(f"{'Read P99 Latency (ms)':<25} | {med_skv_r_p99:<14.3f} | {med_sql_r_p99:<14.3f} | -")
    print(f"{'Missing Key Ops/sec':<25} | {med_skv_m_ops:<14.2f} | {med_sql_m_ops:<14.2f} | {med_skv_m_ops/med_sql_m_ops:.2f}x Bloom Speedup")
    print(f"{'Missing P99 Latency (ms)':<25} | {med_skv_m_p99:<14.3f} | {med_sql_m_p99:<14.3f} | -")
    print("=" * 70)

    # Save structured telemetry JSON
    with open("benchmark_results.json", "w", encoding="utf-8") as f:
        json.dump(raw_results, f, indent=2)
    print("[STATUS] Raw telemetry data saved to: benchmark_results.json")

    # Generate 4-panel dashboard
    fig, axes = plt.subplots(2, 2, figsize=(14, 10), dpi=300)
    plt.subplots_adjust(hspace=0.35, wspace=0.25)

    # Subplot 1: Throughput Comparison
    ax1 = axes[0, 0]
    cats = ["Write Ops/Sec", "Read Ops/Sec", "Missing Key Ops/Sec"]
    lsm_thru = [med_skv_w_ops, med_skv_r_ops, med_skv_m_ops]
    sql_thru = [med_sql_w_ops, med_sql_r_ops, med_sql_m_ops]
    x = np.arange(len(cats))
    width = 0.35
    ax1.bar(x - width/2, lsm_thru, width, label="StrataKV (Ours)", color="#2ecc71", edgecolor="black")
    ax1.bar(x + width/2, sql_thru, width, label="SQLite", color="#e74c3c", edgecolor="black")
    ax1.set_ylabel("Operations / Second", fontweight="bold")
    ax1.set_title("Throughput Comparison (Higher is Better)", fontweight="bold")
    ax1.set_xticks(x)
    ax1.set_xticklabels(cats, fontweight="bold")
    ax1.legend()
    ax1.grid(axis="y", linestyle="--", alpha=0.5)

    # Subplot 2: Tail Latency (P99)
    ax2 = axes[0, 1]
    cats_p99 = ["Write P99", "Read P99", "Missing Key P99"]
    lsm_p99 = [med_skv_w_p99, med_skv_r_p99, med_skv_m_p99]
    sql_p99 = [med_sql_w_p99, med_sql_r_p99, med_sql_m_p99]
    ax2.bar(x - width/2, lsm_p99, width, label="StrataKV", color="#2ecc71", edgecolor="black")
    ax2.bar(x + width/2, sql_p99, width, label="SQLite", color="#e74c3c", edgecolor="black")
    ax2.set_ylabel("P99 Latency (ms)", fontweight="bold")
    ax2.set_title("Tail Latency (P99, Lower is Better)", fontweight="bold")
    ax2.set_xticks(x)
    ax2.set_xticklabels(cats_p99, fontweight="bold")
    ax2.set_yscale("log")
    ax2.legend()
    ax2.grid(axis="y", linestyle="--", alpha=0.5)

    # Subplot 3: Latency Distribution with Explicit Sample Annotation
    ax3 = axes[1, 0]
    np.random.seed(42)
    sample_indices = np.random.choice(NUM_OPERATIONS, 500, replace=False)
    lsm_samples = np.array(last_stratakv_sample_latencies)[sample_indices] * 1000.0
    sql_samples = np.array(last_sqlite_sample_latencies)[sample_indices] * 1000.0

    boxplot_labels = ["StrataKV Writes", "SQLite Writes"]
    try:
        ax3.boxplot(
            [lsm_samples, sql_samples],
            tick_labels=boxplot_labels,
            patch_artist=True,
            boxprops=dict(facecolor="#3498db", color="black"),
            medianprops=dict(color="red", linewidth=1.5),
        )
    except TypeError:
        ax3.boxplot(
            [lsm_samples, sql_samples],
            labels=boxplot_labels,
            patch_artist=True,
            boxprops=dict(facecolor="#3498db", color="black"),
            medianprops=dict(color="red", linewidth=1.5),
        )

    ax3.set_ylabel("Write Latency (ms)", fontweight="bold")
    ax3.set_title("Write Latency Distribution (500-op Sample)", fontweight="bold")
    ax3.set_yscale("log")
    ax3.grid(axis="y", linestyle="--", alpha=0.5)

    # Subplot 4: Pruning Speedup without Hardware Overclaims
    ax4 = axes[1, 1]
    bf_speedup = med_skv_m_ops / max(med_sql_m_ops, 1)
    ax4.bar(["Bloom Filter Speedup"], [bf_speedup], color="#9b59b6", width=0.4, edgecolor="black")
    ax4.set_ylabel("Throughput Multiple (x)", fontweight="bold")
    ax4.set_title("Missing-Key Lookup Speedup via Bloom Filter", fontweight="bold")
    ax4.text(
        0,
        bf_speedup / 2,
        f"{bf_speedup:.2f}x Speedup\n(SSTable Lookups Pruned)",
        ha="center",
        va="center",
        color="white",
        fontweight="bold",
        fontsize=11,
    )
    ax4.grid(axis="y", linestyle="--", alpha=0.5)

    dashboard_path = "benchmark_dashboard.png"
    plt.suptitle("StrataKV vs SQLite Storage Engine Empirical Benchmark", fontsize=15, fontweight="bold", y=0.98)
    fig.savefig(dashboard_path, bbox_inches="tight")
    plt.close(fig)

    cleanup()
    print(f"[STATUS] Visualization dashboard saved to: {dashboard_path}")


if __name__ == "__main__":
    run_benchmark()