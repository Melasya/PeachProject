"""Replay a small, bounded sample of payment events into the SQS queue.

    python producer_sqs.py --queue-url URL --limit 1500 --rate 20

The same replay as the local producer: sorted by receivedAt, a configurable rate. What differs is what
SQS offers: no message key and no partitions, so invoiceId has nothing to steer, and a standard queue
does not promise to hand the messages over in this order. --limit has a hard maximum to bound the cost.
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
BATCH_SIZE = 10  # the SQS maximum per SendMessageBatch


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue-url", required=True)
    parser.add_argument("--file", default=str(Path(__file__).parent.parent / "streaming/data/events.jsonl"))
    parser.add_argument("--limit", type=int, default=1500)
    parser.add_argument("--skip", type=int, default=0, help="start after this many events, for a second sample")
    parser.add_argument("--rate", type=float, default=20.0, help="events per second")
    parser.add_argument("--failure-rate", type=float, help="to test the alert, for example 0.3")
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()
    if args.limit > HARD_LIMIT:
        sys.exit(f"--limit is capped at {HARD_LIMIT}: this is a bounded sample")

    client = boto3.client("sqs", region_name=args.region)
    events = load_events(args.file, args.skip + args.limit)[args.skip :]
    print(f"sending {len(events)} events at {args.rate:g} events/s", flush=True)

    started = time.monotonic()
    sent = failed = 0
    for start in range(0, len(events), BATCH_SIZE):
        entries = []
        for number, event in enumerate(events[start : start + BATCH_SIZE]):
            if args.failure_rate is not None:
                event = force_failure_rate(event, args.failure_rate)
            entries.append({"Id": str(number), "MessageBody": json.dumps(event, ensure_ascii=False)})
        response = client.send_message_batch(QueueUrl=args.queue_url, Entries=entries)
        failed += len(response.get("Failed", []))
        sent += len(response.get("Successful", []))

        delay = started + (start + BATCH_SIZE) / args.rate - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        if sent % 500 == 0:
            print(f"sent {sent}/{len(events)}", flush=True)

    elapsed = time.monotonic() - started
    print(f"done: {sent} accepted by SQS, {failed} refused, in {elapsed:.1f}s ({sent / elapsed:.1f} events/s)", flush=True)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
