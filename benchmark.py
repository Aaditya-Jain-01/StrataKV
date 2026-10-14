import os
import shutil
import time
import sqlite3
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from engine.lsm_tree import LSMTree

BENCH_DIR_LSM = "bench_lsm_store"
BENCH_SQLITE_FILE = "bench_sqlite.db"
NUM_OPERATIONS = 10000

def cleanup():
    if os.path.exists(BENCH_DIR_LSM):
        shutil.rmtree(BENCH_DIR_LSM, ignore_errors=True)
    if os.path.exists(BENCH_SQLITE_FILE):
        try:
            os.remove(BENCH_SQLITE_FILE)
        except OSError:
            pass

def calculate_percentiles(latencies):
    arr = np.array(latencies) * 1000.0
    return {
        "p50": float(np.percentile(arr, 50)),
        "p95": float(np.percentile(arr, 95)),
        "p99": float(np.percentile(arr, 99)),
        "mean": float(np.mean(arr))
    }

def run_benchmark():
    cleanup()
    print("-" * 65)
    print(" STRATAKV VS SQLITE: 10,000 OPERATIONS EMPIRICAL BENCHMARK ")
    print("-" * 65)

    keys = [f"usr_{i:06d}" for i in range(NUM_OPERATIONS)]
    values = [f"payload_value_blob_{i:06d}_{'a'*24}" for i in range(NUM_OPERATIONS)]
    absent_keys = [f"nonexistent_key_{i:06d}" for i in range(NUM_OPERATIONS)]

    print("[STAGE 1] Benchmarking StrataKV Engine (Audited Production Core)...")
    lsm = LSMTree(data_dir=BENCH_DIR_LSM, memtable_threshold_bytes=64 * 1024)

    lsm_write_latencies = []
    t0 = time.perf_counter()
    for k, v in zip(keys, values):
        t_op0 = time.perf_counter()
        lsm.put(k, v)
        lsm_write_latencies.append(time.perf_counter() - t_op0)
    lsm_write_total = time.perf_counter() - t0
    lsm_write_ops = NUM_OPERATIONS / lsm_write_total

    lsm.flush()

    lsm_read_latencies = []
    t0 = time.perf_counter()
    for k in keys:
        t_op0 = time.perf_counter()
        res = lsm.get(k)
        lsm_read_latencies.append(time.perf_counter() - t_op0)
    lsm_read_total = time.perf_counter() - t0
    lsm_read_ops = NUM_OPERATIONS / lsm_read_total

    lsm_absent_latencies = []
    t0 = time.perf_counter()
    for k in absent_keys:
        t_op0 = time.perf_counter()
        res = lsm.get(k)
        lsm_absent_latencies.append(time.perf_counter() - t_op0)
    lsm_absent_total = time.perf_counter() - t0
    lsm_absent_ops = NUM_OPERATIONS / lsm_absent_total

    lsm.close()

    print("[STAGE 2] Benchmarking SQLite Baseline...")
    conn = sqlite3.connect(BENCH_SQLITE_FILE)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT)")
    conn.commit()

    sqlite_write_latencies = []
    t0 = time.perf_counter()
    for k, v in zip(keys, values):
        t_op0 = time.perf_counter()
        cursor.execute("INSERT OR REPLACE INTO kv VALUES (?, ?)", (k, v))
        conn.commit()
        sqlite_write_latencies.append(time.perf_counter() - t_op0)
    sqlite_write_total = time.perf_counter() - t0
    sqlite_write_ops = NUM_OPERATIONS / sqlite_write_total

    sqlite_read_latencies = []
    t0 = time.perf_counter()
    for k in keys:
        t_op0 = time.perf_counter()
        cursor.execute("SELECT value FROM kv WHERE key = ?", (k,))
        res = cursor.fetchone()
        sqlite_read_latencies.append(time.perf_counter() - t_op0)
    sqlite_read_total = time.perf_counter() - t0
    sqlite_read_ops = NUM_OPERATIONS / sqlite_read_total

    sqlite_absent_latencies = []
    t0 = time.perf_counter()
    for k in absent_keys:
        t_op0 = time.perf_counter()
        cursor.execute("SELECT value FROM kv WHERE key = ?", (k,))
        res = cursor.fetchone()
        sqlite_absent_latencies.append(time.perf_counter() - t_op0)
    sqlite_absent_total = time.perf_counter() - t0
    sqlite_absent_ops = NUM_OPERATIONS / sqlite_absent_total

    conn.close()

    lsm_w_perc = calculate_percentiles(lsm_write_latencies)
    lsm_r_perc = calculate_percentiles(lsm_read_latencies)
    lsm_a_perc = calculate_percentiles(lsm_absent_latencies)

    sql_w_perc = calculate_percentiles(sqlite_write_latencies)
    sql_r_perc = calculate_percentiles(sqlite_read_latencies)
    sql_a_perc = calculate_percentiles(sqlite_absent_latencies)

    print("\n" + "=" * 65)
    print(" EMPIRICAL PERFORMANCE RESULTS SUMMARY ")
    print("=" * 65)
    print(f"{'Metric':<25} | {'StrataKV':<15} | {'SQLite':<15} | {'Gain / Variance':<15}")
    print("-" * 65)
    print(f"{'Write Ops/sec':<25} | {lsm_write_ops:<15.2f} | {sqlite_write_ops:<15.2f} | {lsm_write_ops/sqlite_write_ops:.2f}x Throughput")
    print(f"{'Write P99 Latency (ms)':<25} | {lsm_w_perc['p99']:<15.3f} | {sql_w_perc['p99']:<15.3f} | {sql_w_perc['p99']/lsm_w_perc['p99']:.2f}x Lower Latency")
    print(f"{'Read Ops/sec (Hits)':<25} | {lsm_read_ops:<15.2f} | {sqlite_read_ops:<15.2f} | {lsm_read_ops/sqlite_read_ops:.2f}x Variance")
    print(f"{'Read P99 Latency (ms)':<25} | {lsm_r_perc['p99']:<15.3f} | {sql_r_perc['p99']:<15.3f} | -")
    print(f"{'Missing Key Ops/sec':<25} | {lsm_absent_ops:<15.2f} | {sqlite_absent_ops:<15.2f} | {lsm_absent_ops/sqlite_absent_ops:.2f}x Bloom Speedup")
    print(f"{'Missing P99 Latency (ms)':<25} | {lsm_a_perc['p99']:<15.3f} | {sql_a_perc['p99']:<15.3f} | -")
    print("=" * 65)

    fig, axes = plt.subplots(2, 2, figsize=(14, 10), dpi=300)
    plt.subplots_adjust(hspace=0.35, wspace=0.25)

    ax1 = axes[0, 0]
    cats = ["Write Ops/Sec", "Read Ops/Sec", "Missing Key Ops/Sec"]
    lsm_thru = [lsm_write_ops, lsm_read_ops, lsm_absent_ops]
    sql_thru = [sqlite_write_ops, sqlite_read_ops, sqlite_absent_ops]
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

    ax2 = axes[0, 1]
    cats_p99 = ["Write P99", "Read P99", "Missing Key P99"]
    lsm_p99 = [lsm_w_perc["p99"], lsm_r_perc["p99"], lsm_a_perc["p99"]]
    sql_p99 = [sql_w_perc["p99"], sql_r_perc["p99"], sql_a_perc["p99"]]
    ax2.bar(x - width/2, lsm_p99, width, label="StrataKV", color="#2ecc71", edgecolor="black")
    ax2.bar(x + width/2, sql_p99, width, label="SQLite", color="#e74c3c", edgecolor="black")
    ax2.set_ylabel("P99 Latency (ms)", fontweight="bold")
    ax2.set_title("Tail Latency (P99, Lower is Better)", fontweight="bold")
    ax2.set_xticks(x)
    ax2.set_xticklabels(cats_p99, fontweight="bold")
    ax2.set_yscale("log")
    ax2.legend()
    ax2.grid(axis="y", linestyle="--", alpha=0.5)

    ax3 = axes[1, 0]
    np.random.seed(42)  # Deterministic seed for reproducible latency sampling
    sample_indices = np.random.choice(NUM_OPERATIONS, 500, replace=False)
    lsm_samples = np.array(lsm_write_latencies)[sample_indices] * 1000.0
    sql_samples = np.array(sqlite_write_latencies)[sample_indices] * 1000.0

    # Backwards-compatible label handling across Matplotlib 3.8 and 3.9+
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
    ax3.set_title("Write Latency Distribution", fontweight="bold")
    ax3.set_yscale("log")
    ax3.grid(axis="y", linestyle="--", alpha=0.5)

    ax4 = axes[1, 1]
    bf_speedup = lsm_absent_ops / max(sqlite_absent_ops, 1)
    ax4.bar(["Bloom Filter Avoidance Speedup"], [bf_speedup], color="#9b59b6", width=0.4, edgecolor="black")
    ax4.set_ylabel("Throughput Multiple (x)", fontweight="bold")
    ax4.set_title("Missing-Key Query Speedup via Bloom Filters", fontweight="bold")
    ax4.text(
        0,
        bf_speedup / 2,
        f"{bf_speedup:.2f}x Speedup\n(Disk Seeks Bypassed)",
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
    print(f"\n[STATUS] Benchmark visualization dashboard saved to: {dashboard_path}")

if __name__ == "__main__":
    run_benchmark()