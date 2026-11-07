"""Replay one day of payment events into the Kafka topic `payments`.

    python producer.py --file data/events.jsonl --rate 20

- Events are sorted by receivedAt: the order the payment system saw them.
- invoiceId is the message key, so all events of an invoice land on one partition, in order.
- --rate is the replay speed in events per second.
- --failure-rate 0.3 is Experiment C: it rewrites terminal events so 30% of payments fail.
"""

import argparse
import hashlib
import json
import time

from kafka import KafkaProducer


def load_events(path: str, limit: int | None) -> list[dict]:
    with open(path, encoding="utf-8") as file:
        events = [json.loads(line) for line in file if line.strip()]
    events.sort(key=lambda event: event["receivedAt"])  # stable: ties keep file order
    return events[:limit] if limit else events


def payment_fails(invoice_id: str, failure_rate: float) -> bool:
    """The same invoice always gets the same answer, so its duplicates stay exact duplicates."""
    digest = hashlib.sha256(invoice_id.encode()).digest()
    return digest[0] / 256 < failure_rate


def force_failure_rate(event: dict, failure_rate: float) -> dict:
    """Experiment C: only terminal events change; created and processing stay as they were."""
    if event["status"] not in ("success", "failure"):
        return event
    event = dict(event)
    if payment_fails(event["invoiceId"], failure_rate):
        event["status"] = "failure"
        event["failureReason"] = event.get("failureReason") or "experiment_c"
        event["finalAmount"] = 0
    else:
        event["status"] = "success"
        event.pop("failureReason", None)
        event["finalAmount"] = event["amount"]
    return event


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", default="data/events.jsonl")
    parser.add_argument("--rate", type=float, default=20.0, help="events per second")
    parser.add_argument("--limit", type=int, help="replay only the first N events")
    parser.add_argument("--failure-rate", type=float, help="Experiment C, for example 0.3")
    parser.add_argument("--topic", default="payments")
    parser.add_argument("--brokers", default="localhost:19092")
    args = parser.parse_args()

    events = load_events(args.file, args.limit)
    print(f"loaded {len(events)} events, replaying at {args.rate:g} events/s", flush=True)

    producer = KafkaProducer(
        bootstrap_servers=args.brokers,
        key_serializer=str.encode,
        value_serializer=lambda value: json.dumps(value, ensure_ascii=False).encode(),
        acks="all",
        batch_size=512,  # about one event per record batch, so a consumer can read partitions in step
        api_version=(2, 8, 0),  # Redpanda speaks this; skips the version probe, which can time out
    )

    started = time.monotonic()
    for sent, event in enumerate(events, start=1):
        if args.failure_rate is not None:
            event = force_failure_rate(event, args.failure_rate)
        producer.send(args.topic, key=event["invoiceId"], value=event)

        # Pacing: sleep until this event's place in the schedule. Never drifts.
        delay = started + sent / args.rate - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        if sent % 1000 == 0:
            elapsed = time.monotonic() - started
            print(f"sent {sent}/{len(events)} ({sent / elapsed:.1f} events/s)", flush=True)

    producer.flush()
    elapsed = time.monotonic() - started
    print(f"done: {len(events)} events in {elapsed:.1f}s ({len(events) / elapsed:.1f} events/s)", flush=True)


if __name__ == "__main__":
    main()
