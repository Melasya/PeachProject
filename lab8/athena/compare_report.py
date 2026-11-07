"""Build results/performance_comparison.csv from Athena's own numbers in results/metrics.csv."""

import csv
import pathlib
import statistics

RESULTS = pathlib.Path(__file__).parent / "results"
rows = list(csv.DictReader((RESULTS / "metrics.csv").open(encoding="utf-8")))


def runs(query: str, fmt: str) -> list[dict]:
    return [row for row in rows if row["label"].startswith(f"compare_{query}_{fmt}_run")]


fields = [
    "query", "json_runs_s", "parquet_runs_s", "json_median_s", "parquet_median_s", "speedup",
    "json_scanned_bytes", "parquet_scanned_bytes", "scan_reduction", "json_cost_usd", "parquet_cost_usd", "cost_reduction",
]  # fmt: skip
with (RESULTS / "performance_comparison.csv").open("w", newline="", encoding="utf-8") as file:
    writer = csv.DictWriter(file, fieldnames=fields)
    writer.writeheader()
    for query in ("turnover", "failure_rate", "peak_hours"):
        json_runs, parquet_runs = runs(query, "json"), runs(query, "parquet")
        json_median = statistics.median(int(row["total_ms"]) for row in json_runs) / 1000
        parquet_median = statistics.median(int(row["total_ms"]) for row in parquet_runs) / 1000
        json_bytes = int(json_runs[0]["data_scanned_bytes"])
        parquet_bytes = int(parquet_runs[0]["data_scanned_bytes"])
        json_cost = float(json_runs[0]["estimated_cost_usd"])
        parquet_cost = float(parquet_runs[0]["estimated_cost_usd"])
        row = {
            "query": query,
            "json_runs_s": " ".join(f"{int(r['total_ms']) / 1000:.2f}" for r in json_runs),
            "parquet_runs_s": " ".join(f"{int(r['total_ms']) / 1000:.2f}" for r in parquet_runs),
            "json_median_s": f"{json_median:.2f}",
            "parquet_median_s": f"{parquet_median:.2f}",
            "speedup": f"{json_median / parquet_median:.1f}x",
            "json_scanned_bytes": json_bytes,
            "parquet_scanned_bytes": parquet_bytes,
            "scan_reduction": f"{json_bytes / parquet_bytes:.0f}x",
            "json_cost_usd": f"{json_cost:.6f}",
            "parquet_cost_usd": f"{parquet_cost:.6f}",
            "cost_reduction": f"{json_cost / parquet_cost:.0f}x",
        }
        writer.writerow(row)
        print(row)
total = sum(float(row["estimated_cost_usd"]) for row in rows)
scanned = sum(int(row["data_scanned_bytes"]) for row in rows)
print(f"all {len(rows)} queries: {scanned / 1024**3:.2f} GiB scanned, ${total:.4f} estimated")
