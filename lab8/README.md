# Lab 8: Big Data on AWS, the Uklon example

Separate from the Peach application: nothing here is imported by it, and none of it touches Peach's
infrastructure. AWS resources are named `ooad-lab8-*` or `lab8*`.

| Folder | What | State |
|---|---|---|
| `athena/` | Part 2: SQL, a runner that records Athena's real metrics, S3 and Glue setup | Run |
| `streaming/` | Part 3: Redpanda + Console (Compose), producer, consumer, the four experiments | Run locally |
| `cloud_sqs/` | Part 4 as deployed: producer -> SQS -> Lambda -> S3 -> Glue -> Athena, Lambda -> SNS | Deployed; an **alternative**, not equivalent to the request |
| `cloud/` | Part 4 as requested: Kinesis + Firehose. **Never deployed:** this AWS Free Plan account gets `SubscriptionRequiredException` for both | Reference only |

## Part 2

```bash
bash athena/setup_part2.sh bucket     # private bucket
bash athena/setup_part2.sh copy       # server-side copy of payments/
bash athena/setup_part2.sh workgroup  # Athena workgroup with a scan limit
python athena/run_athena.py setup     # table, partitions, counts, view
python athena/run_athena.py analytics
python athena/run_athena.py curate    # CTAS to Parquet under curated/
python athena/run_athena.py compare   # JSON vs Parquet, three runs each
python athena/compare_report.py
bash athena/setup_part2.sh crawler
```

## Part 3

```bash
cd streaming
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
docker compose up -d                  # Redpanda; Console on http://localhost:8080; topic payments, 3 partitions
python -m pytest test_windowing.py
python producer.py --rate 20 --limit 1200
python consumer.py --group demo --allowed-lateness 120
python experiments.py A               # or B, C, D; logs go to evidence/
```

Put one day of events in `streaming/data/events.jsonl`. Downloading `payments/dt=2026-10-15/events.jsonl` is
simplest. On a slow connection, `athena/sql/08_unload_one_day.sql` plus `unpack_one_day.py` gives the same rows gzipped.

`windowing.py` is the part to read first: one-minute event-time windows, the watermark, late events.

## Part 4

```bash
bash cloud_sqs/deploy.sh
python cloud_sqs/producer_sqs.py --queue-url <QueueUrl> --limit 1500 --rate 20
python cloud_sqs/producer_sqs.py --queue-url <QueueUrl> --skip 1500 --limit 1500 --failure-rate 0.3
python athena/run_athena.py stream
aws cloudformation delete-stack --stack-name ooad-lab8-sqs     # when finished
```
