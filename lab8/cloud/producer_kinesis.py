"""Replay a small, bounded sample of payment events into Kinesis Data Streams.

    python producer_kinesis.py --file ../streaming/data/events.jsonl --limit 3000 --rate 20

The same replay as the local producer, except for the client: sorted by receivedAt, invoiceId as
the partition key, a configurable rate. --limit has a hard maximum so a typo cannot run up a bill.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import boto3

# The local producer's helpers; both replay the same way.
sys.path.append(str(Path(__file__).parent.parent / "streaming"))
from producer import force_failure_rate, load_events

HARD_LIMIT = 10_000
BATCH_SIZE = 100


def send_batch(client, stream: str, batch: list[dict]) -> int:
    """PutRecords can fail for single records (throttling); send those again. Returns retries."""
    retries = 0
    while batch:
        response = client.put_records(StreamName=stream, Records=batch)
        if response["FailedRecordCount"] == 0:
            return retries
        batch = [record for record, result in zip(batch, response["Records"]) if "ErrorCode" in result]
        retries += len(batch)
        time.sleep(0.5)
    return retries


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", default="../streaming/data/events.jsonl")
    parser.add_argument("--stream", default="ooad-lab8-payments")
    parser.add_argument("--limit", type=int, default=3000)
    parser.add_argument("--rate", type=float, default=20.0, help="events per second")
    parser.add_argument("--failure-rate", type=float, help="to test the alert, for example 0.3")
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()
    if args.limit > HARD_LIMIT:
        sys.exit(f"--limit is capped at {HARD_LIMIT}: this is a bounded sample")

    client = boto3.client("kinesis", region_name=args.region)
    events = load_events(args.file, args.limit)
    print(f"sending {len(events)} events to {args.stream} at {args.rate:g} events/s", flush=True)

    started = time.monotonic()
    sent = retries = 0
    for start in range(0, len(events), BATCH_SIZE):
        batch = []
        for event in events[start : start + BATCH_SIZE]:
            if args.failure_rate is not None:
                event = force_failure_rate(event, args.failure_rate)
            # The trailing newline makes the S3 objects JSONL; Firehose adds no delimiter itself.
            data = json.dumps(event, ensure_ascii=False) + "\n"
            batch.append({"Data": data.encode(), "PartitionKey": event["invoiceId"]})
        retries += send_batch(client, args.stream, batch)
        sent += len(batch)
        delay = started + sent / args.rate - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        print(f"sent {sent}/{len(events)}", flush=True)

    elapsed = time.monotonic() - started
    print(f"done: {sent} events in {elapsed:.1f}s, {retries} records retried", flush=True)


if __name__ == "__main__":
    main()
