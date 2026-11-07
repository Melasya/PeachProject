"""Run the Lab 8 SQL files in Athena and keep the evidence: the result CSV and the real metrics.

    python run_athena.py setup       # database, table, partitions, counts, view, profile
    python run_athena.py analytics   # questions 1-6 over the JSON view
    python run_athena.py curate      # CTAS to Parquet, then verify it
    python run_athena.py compare     # turnover, failure rate, peak hours: JSON vs Parquet, 3 runs each
    python run_athena.py stream      # Part 4: the events delivered through SQS -> Lambda -> S3

Every query gets one line in results/metrics.csv, taken from Athena's own statistics.
"""

import csv
import os
import pathlib
import sys
import time

import boto3

BUCKET = os.environ.get("LAB8_BUCKET", "ooad-lab8-melasya")
WORKGROUP = "lab8"
HERE = pathlib.Path(__file__).parent
SQL = HERE / "sql"
RESULTS = HERE / "results"
PRICE_PER_TB = 5.0
MINIMUM_BILLED_BYTES = 10 * 1024**2  # Athena bills at least 10 MB per query

athena = boto3.client("athena", region_name="us-east-1")
s3 = boto3.client("s3", region_name="us-east-1")


def run(sql_file: str, label: str, table: str | None = None, keep_csv: bool = True) -> dict:
    text = (SQL / sql_file).read_text(encoding="utf-8").replace("{bucket}", BUCKET)
    if table:
        text = text.replace("{table}", table)
    query_id = athena.start_query_execution(QueryString=text, WorkGroup=WORKGROUP)["QueryExecutionId"]

    while True:
        execution = athena.get_query_execution(QueryExecutionId=query_id)["QueryExecution"]
        state = execution["Status"]["State"]
        if state not in ("QUEUED", "RUNNING"):
            break
        time.sleep(1)
    if state != "SUCCEEDED":
        sys.exit(f"{label} {state}: {execution['Status'].get('StateChangeReason')}")

    stats = execution["Statistics"]
    scanned = stats.get("DataScannedInBytes", 0)
    billed = max(scanned, MINIMUM_BILLED_BYTES) if scanned else 0
    row = {
        "label": label,
        "sql_file": sql_file,
        "query_execution_id": query_id,
        "engine_ms": stats.get("EngineExecutionTimeInMillis"),
        "total_ms": stats.get("TotalExecutionTimeInMillis"),
        "data_scanned_bytes": scanned,
        "data_scanned_mb": round(scanned / 1024**2, 2),
        "estimated_cost_usd": round(billed / 1024**4 * PRICE_PER_TB, 6),
    }
    print(f"{label:34} {row['total_ms'] / 1000:7.1f}s  {row['data_scanned_mb']:10.2f} MB  ${row['estimated_cost_usd']:.5f}")

    output = execution["ResultConfiguration"]["OutputLocation"]
    if keep_csv and output.endswith(".csv"):
        key = output.removeprefix(f"s3://{BUCKET}/")
        s3.download_file(BUCKET, key, str(RESULTS / f"{label}.csv"))

    metrics = RESULTS / "metrics.csv"
    is_new = not metrics.exists()
    with metrics.open("a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=row)
        if is_new:
            writer.writeheader()
        writer.writerow(row)
    return row


def main() -> None:
    RESULTS.mkdir(exist_ok=True)
    step = sys.argv[1] if len(sys.argv) > 1 else ""
    if step == "setup":
        run("01_create_database.sql", "01_create_database")
        run("02_create_payment_events.sql", "02_create_table")
        run("03_load_partitions.sql", "03_load_partitions")
        run("04_verify_counts.sql", "04_verify_counts")
        run("05_create_view_payments.sql", "05_create_view")
        run("q7_data_profile.sql", "q7_data_profile")
    elif step == "analytics":
        run("q1_unique_and_duplicates.sql", "q1_unique_and_duplicates")
        run("q2_turnover.sql", "q2_turnover_json", "lab8.payments")
        run("q3_failure_rate.sql", "q3_failure_rate_json", "lab8.payments")
        run("q4_peak_hours.sql", "q4_peak_hours_json", "lab8.payments")
        run("q5_late_over_60s.sql", "q5_late_over_60s")
        run("q6_late_over_1h.sql", "q6_late_over_1h")
    elif step == "curate":
        run("06_ctas_parquet.sql", "06_ctas_parquet")
        run("07_verify_curated.sql", "07_verify_curated")
    elif step == "compare":
        for attempt in (1, 2, 3):
            for name, sql_file in (
                ("turnover", "q2_turnover.sql"),
                ("failure_rate", "q3_failure_rate.sql"),
                ("peak_hours", "q4_peak_hours.sql"),
            ):
                run(sql_file, f"compare_{name}_json_run{attempt}", "lab8.payments", keep_csv=attempt == 1)
                run(sql_file, f"compare_{name}_parquet_run{attempt}", "lab8.payments_curated", keep_csv=attempt == 1)
    elif step == "stream":
        run("s1_stream_counts.sql", "s1_stream_counts")
        run("s2_failure_rate_by_batch_object.sql", "s2_failure_rate_by_batch_object")
        run("s3_failure_rate_by_method.sql", "s3_failure_rate_by_method")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
