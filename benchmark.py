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
REPETITIONS = 5


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


def add_bar_labels(ax, fmt: str = "{:,.0f}", stagger: bool = False) -> None:
    """Adds exact numerical text labels directly above plot bars with optional staggering."""
    for idx, rect in enumerate(ax.patches):
        height = rect.get_height()
        if height > 0:
            # Stagger vertical offsets for adjacent bars on crowded log charts
            y_offset = (13 if idx % 2 == 1 else 3) if stagger else 4
            ax.annotate(
                fmt.format(height),
                xy=(rect.get_x() + rect.get_width() / 2, height),
                xytext=(0, y_offset),
                textcoords="offset points",
                ha="center",
                va="bottom",
                fontsize=7.5,
                fontweight="bold",
            )


def run_benchmark() -> None:
    cleanup()
    print("-" * 78)
    print(f" STRATAKV VS SQLITE: STATISTICAL BENCHMARK ({REPETITIONS} RUNS x {NUM_OPERATIONS:,} OPS) ")
    print("-" * 78)

    # Directly query default SQLite PRAGMAs from active engine connection
    conn_probe = sqlite3.connect(BENCH_SQLITE_FILE)
    c_probe = conn_probe.cursor()
    c_probe.execute("PRAGMA journal_mode;")
    sqlite_journal_mode = str(c_probe.fetchone()[0]).upper()
    c_probe.execute("PRAGMA synchronous;")
    sqlite_sync_setting = int(c_probe.fetchone()[0])
    conn_probe.close()
    cleanup()

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
            "sqlite_journal_mode": sqlite_journal_mode,
            "sqlite_synchronous": sqlite_sync_setting,
            "sqlite_transaction_model": "Autocommit per insert (commit on every write matching WAL fsync)",
            "numpy_version": np.__version__,
            "matplotlib_version": matplotlib.__version__,
            "num_operations": NUM_OPERATIONS,
            "repetitions": REPETITIONS,
            "workload_execution_model": "Alternating engine order; sequential stages per run (writes -> uncompacted reads -> compaction -> compacted reads -> missing reads)",
            "cache_state": "Intra-run warm OS filesystem page cache for reads post-write and post-compaction",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        },
        "stratakv": {"writes": [], "reads_uncompacted": [], "reads_compacted": [], "missing_compacted": []},
        "sqlite": {"writes": [], "reads": [], "missing": []},
    }

    # Accumulate all write latencies across all 5 runs for pooled box plot
    all_stratakv_write_latencies: list[float] = []
    all_sqlite_write_latencies: list[float] = []

    for run_idx in range(1, REPETITIONS + 1):
        stratakv_first = (run_idx % 2 == 1)
        order_desc = "StrataKV -> SQLite" if stratakv_first else "SQLite -> StrataKV"
        print(f"\n[ITERATION {run_idx}/{REPETITIONS}] Executing benchmarks ({order_desc})...")
        cleanup()

        def bench_stratakv():
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

            lsm.flush()

            # 2. Uncompacted Reads
            t0 = time.perf_counter()
            ru_lats = []
            for k in keys:
                t_op = time.perf_counter()
                res = lsm.get(k)
                ru_lats.append(time.perf_counter() - t_op)
                if res != expected_kv[k]:
                    raise AssertionError(f"Correctness failure in StrataKV on key '{k}'")
            ru_total = time.perf_counter() - t0
            ru_ops = NUM_OPERATIONS / ru_total

            # 3. Compacted Reads
            lsm.compact()
            t0 = time.perf_counter()
            rc_lats = []
            for k in keys:
                t_op = time.perf_counter()
                res = lsm.get(k)
                rc_lats.append(time.perf_counter() - t_op)
                if res != expected_kv[k]:
                    raise AssertionError(f"Correctness failure post-compaction on key '{k}'")
            rc_total = time.perf_counter() - t0
            rc_ops = NUM_OPERATIONS / rc_total

            # 4. Missing-Key Reads (Compacted DB)
            t0 = time.perf_counter()
            m_lats = []
            for k in absent_keys:
                t_op = time.perf_counter()
                res = lsm.get(k)
                m_lats.append(time.perf_counter() - t_op)
                if res is not None:
                    raise AssertionError(f"Correctness failure: absent key '{k}' returned non-None")
            m_total = time.perf_counter() - t0
            m_ops = NUM_OPERATIONS / m_total

            lsm.close()
            return (w_ops, w_lats), (ru_ops, ru_lats), (rc_ops, rc_lats), (m_ops, m_lats)

        def bench_sqlite():
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

            # 2. Point Reads
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
            return (sql_w_ops, sql_w_lats), (sql_r_ops, sql_r_lats), (sql_m_ops, sql_m_lats)

        if stratakv_first:
            skv_w, skv_ru, skv_rc, skv_m = bench_stratakv()
            sql_w, sql_r, sql_m = bench_sqlite()
        else:
            sql_w, sql_r, sql_m = bench_sqlite()
            skv_w, skv_ru, skv_rc, skv_m = bench_stratakv()

        raw_results["stratakv"]["writes"].append({"ops_sec": skv_w[0], "metrics": calculate_metrics(skv_w[1])})
        raw_results["stratakv"]["reads_uncompacted"].append({"ops_sec": skv_ru[0], "metrics": calculate_metrics(skv_ru[1])})
        raw_results["stratakv"]["reads_compacted"].append({"ops_sec": skv_rc[0], "metrics": calculate_metrics(skv_rc[1])})
        raw_results["stratakv"]["missing_compacted"].append({"ops_sec": skv_m[0], "metrics": calculate_metrics(skv_m[1])})

        raw_results["sqlite"]["writes"].append({"ops_sec": sql_w[0], "metrics": calculate_metrics(sql_w[1])})
        raw_results["sqlite"]["reads"].append({"ops_sec": sql_r[0], "metrics": calculate_metrics(sql_r[1])})
        raw_results["sqlite"]["missing"].append({"ops_sec": sql_m[0], "metrics": calculate_metrics(sql_m[1])})

        all_stratakv_write_latencies.extend(skv_w[1])
        all_sqlite_write_latencies.extend(sql_w[1])

    # Medians and standard deviations
    skv_w_rates = [r["ops_sec"] for r in raw_results["stratakv"]["writes"]]
    sql_w_rates = [r["ops_sec"] for r in raw_results["sqlite"]["writes"]]
    skv_m_rates = [r["ops_sec"] for r in raw_results["stratakv"]["missing_compacted"]]
    sql_m_rates = [r["ops_sec"] for r in raw_results["sqlite"]["missing"]]

    med_skv_w_ops = float(np.median(skv_w_rates))
    med_skv_ru_ops = float(np.median([r["ops_sec"] for r in raw_results["stratakv"]["reads_uncompacted"]]))
    med_skv_rc_ops = float(np.median([r["ops_sec"] for r in raw_results["stratakv"]["reads_compacted"]]))
    med_skv_m_ops = float(np.median(skv_m_rates))

    med_sql_w_ops = float(np.median(sql_w_rates))
    med_sql_r_ops = float(np.median([r["ops_sec"] for r in raw_results["sqlite"]["reads"]]))
    med_sql_m_ops = float(np.median(sql_m_rates))

    med_skv_w_p99 = float(np.median([r["metrics"]["p99_ms"] for r in raw_results["stratakv"]["writes"]]))
    med_skv_ru_p99 = float(np.median([r["metrics"]["p99_ms"] for r in raw_results["stratakv"]["reads_uncompacted"]]))
    med_skv_rc_p99 = float(np.median([r["metrics"]["p99_ms"] for r in raw_results["stratakv"]["reads_compacted"]]))
    med_skv_m_p99 = float(np.median([r["metrics"]["p99_ms"] for r in raw_results["stratakv"]["missing_compacted"]]))

    med_sql_w_p99 = float(np.median([r["metrics"]["p99_ms"] for r in raw_results["sqlite"]["writes"]]))
    med_sql_r_p99 = float(np.median([r["metrics"]["p99_ms"] for r in raw_results["sqlite"]["reads"]]))
    med_sql_m_p99 = float(np.median([r["metrics"]["p99_ms"] for r in raw_results["sqlite"]["missing"]]))

    if med_sql_m_ops <= 0:
        raise ValueError(f"Invalid measured SQLite throughput: {med_sql_m_ops}")
    throughput_ratio = med_skv_m_ops / med_sql_m_ops

    print("\n" + "=" * 78)
    print(" EMPIRICAL PERFORMANCE RESULTS SUMMARY (MEDIANS ACROSS 5 RUNS) ")
    print("=" * 78)
    print(f"{'Workload Scenario':<30} | {'StrataKV':<13} | {'SQLite':<13} | {'Multiple':<14}")
    print("-" * 78)
    print(f"{'Write Ops/sec':<30} | {med_skv_w_ops:<13.2f} | {med_sql_w_ops:<13.2f} | {med_skv_w_ops/med_sql_w_ops:.2f}x Throughput")
    print(f"{'Write P99 Latency (ms)':<30} | {med_skv_w_p99:<13.3f} | {med_sql_w_p99:<13.3f} | {med_sql_w_p99/med_skv_w_p99:.2f}x Lower Latency")
    print(f"{'Read (Uncompacted Multi-SST)':<30} | {med_skv_ru_ops:<13.2f} | {med_sql_r_ops:<13.2f} | {med_skv_ru_ops/med_sql_r_ops:.2f}x Variance")
    print(f"{'Read (Compacted Single-SST)':<30} | {med_skv_rc_ops:<13.2f} | {med_sql_r_ops:<13.2f} | {med_skv_rc_ops/med_sql_r_ops:.2f}x Variance")
    print(f"{'Read P99 (Compacted, ms)':<30} | {med_skv_rc_p99:<13.3f} | {med_sql_r_p99:<13.3f} | -")
    print(f"{'Missing-Key Ops/sec (Compacted)':<30} | {med_skv_m_ops:<13.2f} | {med_sql_m_ops:<13.2f} | {throughput_ratio:.2f}x Ratio")
    print(f"{'Missing-Key P99 (Compacted, ms)':<30} | {med_skv_m_p99:<13.3f} | {med_sql_m_p99:<13.3f} | -")
    print("=" * 78)
    print(f"Variability (Write Ops/sec StdDev)      : StrataKV ±{np.std(skv_w_rates):.1f} | SQLite ±{np.std(sql_w_rates):.1f}")
    print(f"Variability (Missing-Key Ops/sec StdDev): StrataKV ±{np.std(skv_m_rates):.1f} | SQLite ±{np.std(sql_m_rates):.1f}")

    with open("benchmark_results.json", "w", encoding="utf-8") as f:
        json.dump(raw_results, f, indent=2)
    print("\n[STATUS] Raw telemetry data saved to: benchmark_results.json")

    fig, axes = plt.subplots(2, 2, figsize=(15, 11), dpi=300)
    plt.subplots_adjust(hspace=0.38, wspace=0.25, bottom=0.08)

    # Panel 1: Throughput Comparison
    ax1 = axes[0, 0]
    cats = ["Write", "Read (Uncomp)", "Read (Comp)", "Missing (Comp)"]
    skv_thru = [med_skv_w_ops, med_skv_ru_ops, med_skv_rc_ops, med_skv_m_ops]
    sql_thru = [med_sql_w_ops, med_sql_r_ops, med_sql_r_ops, med_sql_m_ops]
    x = np.arange(len(cats))
    width = 0.35

    ax1.bar(x - width/2, skv_thru, width, label="StrataKV (Ours)", color="#2ecc71", edgecolor="black")
    ax1.bar(x + width/2, sql_thru, width, label="SQLite", color="#e74c3c", edgecolor="black")
    ax1.set_ylabel("Operations / Second", fontweight="bold")
    ax1.set_title("Throughput Comparison (Higher is Better)", fontweight="bold")
    ax1.set_xticks(x)
    ax1.set_xticklabels(cats, fontweight="bold", fontsize=8)
    ax1.legend()
    ax1.grid(axis="y", linestyle="--", alpha=0.5)
    add_bar_labels(ax1, fmt="{:,.0f}", stagger=False)

    # Panel 2: Tail Latency (P99) with Staggered Labels to Prevent Collisions
    ax2 = axes[0, 1]
    cats_p99 = ["Write P99", "Read (Uncomp)", "Read (Comp)", "Missing (Comp)"]
    skv_p99 = [med_skv_w_p99, med_skv_ru_p99, med_skv_rc_p99, med_skv_m_p99]
    sql_p99 = [med_sql_w_p99, med_sql_r_p99, med_sql_r_p99, med_sql_m_p99]

    ax2.bar(x - width/2, skv_p99, width, label="StrataKV", color="#2ecc71", edgecolor="black")
    ax2.bar(x + width/2, sql_p99, width, label="SQLite", color="#e74c3c", edgecolor="black")
    ax2.set_ylabel("P99 Latency (ms)", fontweight="bold")
    ax2.set_title("Tail Latency (P99, Lower is Better)", fontweight="bold")
    ax2.set_xticks(x)
    ax2.set_xticklabels(cats_p99, fontweight="bold", fontsize=8)
    ax2.set_yscale("log")
    ax2.legend()
    ax2.grid(axis="y", linestyle="--", alpha=0.5)
    # Stagger labels vertically on log scale so adjacent bars do not overlap
    add_bar_labels(ax2, fmt="{:.3f} ms", stagger=True)

    # Panel 3: Latency Distribution (Pooled across all 5 runs)
    ax3 = axes[1, 0]
    np.random.seed(42)
    # Pool all 50,000 writes across 5 runs and sample 500 points deterministically
    sample_indices = np.random.choice(len(all_stratakv_write_latencies), 500, replace=False)
    lsm_samples = np.array(all_stratakv_write_latencies)[sample_indices] * 1000.0
    sql_samples = np.array(all_sqlite_write_latencies)[sample_indices] * 1000.0

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
    ax3.set_title("Write Latency Distribution (500-op Sample Pooled Across 5 Runs)", fontweight="bold", fontsize=9.5)
    ax3.set_yscale("log")
    ax3.grid(axis="y", linestyle="--", alpha=0.5)

    # Panel 4: Missing-Key Lookup Throughput Ratio (Compacted DB)
    ax4 = axes[1, 1]
    ax4.bar(["Missing-Key Ratio (Compacted)"], [throughput_ratio], color="#9b59b6", width=0.4, edgecolor="black")
    ax4.set_ylabel("Throughput Multiple (x)", fontweight="bold")
    ax4.set_title("Missing-Key Lookup Throughput Ratio (Compacted)", fontweight="bold")
    ax4.text(
        0,
        throughput_ratio / 2,
        f"{throughput_ratio:.2f}x vs SQLite\n(Compacted DB, Pruned SSTable Lookups)",
        ha="center",
        va="center",
        color="white",
        fontweight="bold",
        fontsize=10,
    )
    ax4.set_ylim(0, max(throughput_ratio * 1.25, 2.0))
    ax4.grid(axis="y", linestyle="--", alpha=0.5)
    add_bar_labels(ax4, fmt="{:.2f}x", stagger=False)

    env_footer = (
        f"Environment: {platform.system()} {platform.release()} ({platform.machine()}) | "
        f"Python {platform.python_version()} | SQLite {sqlite3.sqlite_version} (journal={sqlite_journal_mode}, sync={sqlite_sync_setting}) | "
        f"Params: {REPETITIONS} Runs x {NUM_OPERATIONS:,} Ops (Sequential Stages)"
    )
    fig.text(0.5, 0.02, env_footer, ha="center", fontsize=8.5, color="#555555", style="italic")

    dashboard_path = "benchmark_dashboard.png"
    plt.suptitle("StrataKV vs SQLite Storage Engine Empirical Benchmark", fontsize=15, fontweight="bold", y=0.98)

    if os.path.exists(dashboard_path):
        try:
            os.remove(dashboard_path)
        except OSError:
            pass

    try:
        fig.savefig(dashboard_path, bbox_inches="tight")
        print(f"[STATUS] Visualization dashboard saved to: {dashboard_path}")
    except OSError:
        fallback_name = "benchmark_dashboard_latest.png"
        fig.savefig(fallback_name, bbox_inches="tight")
        print(f"[WARNING] '{dashboard_path}' is locked by another process.")
        print(f"[STATUS] Saved visualization dashboard to: {fallback_name}")
        if os.path.exists(fallback_name) and not os.path.exists(dashboard_path):
            try:
                os.replace(fallback_name, dashboard_path)
            except OSError:
                pass

    plt.close(fig)
    cleanup()


if __name__ == "__main__":
    run_benchmark()