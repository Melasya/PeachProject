"""SQS -> this function -> S3, and SNS when a payment method's failure rate is too high.

One invocation handles one batch. It stands in for two things the requested design gets from managed
services: writing files to S3 (Firehose) and buffering a batch (the Kinesis trigger's window).

The S3 key is a hash of the batch's message IDs. SQS is at-least-once, so a retried batch overwrites
its own file instead of adding a second copy. A message delivered again in a *different* batch still
produces a duplicate row, which is why the Athena queries deduplicate.
"""

import hashlib
import json
import os
from datetime import UTC, datetime

import boto3

s3 = boto3.client("s3")
sns = boto3.client("sns")


def object_key(message_ids: list[str], now: datetime) -> str:
    digest = hashlib.sha256("".join(sorted(message_ids)).encode()).hexdigest()[:24]
    return f"sqs-stream/dt={now:%Y-%m-%d}/{digest}.jsonl"


def count_finished(events: list[dict]) -> dict[str, dict[str, int]]:
    """Finished payments per method. A duplicate inside the batch is counted once."""
    seen = set()
    counts: dict[str, dict[str, int]] = {}
    for event in events:
        identity = (event["invoiceId"], event["status"], event["modifiedDate"])
        if identity in seen or event["status"] not in ("success", "failure"):
            continue
        seen.add(identity)
        method = counts.setdefault(event["paymentMethod"], {"success": 0, "failure": 0})
        method[event["status"]] += 1
    return counts


def find_alerts(counts: dict, threshold: float, minimum_sample: int) -> list[str]:
    alerts = []
    for method, numbers in sorted(counts.items()):
        finished = numbers["success"] + numbers["failure"]
        rate = numbers["failure"] / finished
        # A rate from three payments says nothing; do not page anyone over it.
        if finished >= minimum_sample and rate > threshold:
            alerts.append(f"{method}: {rate:.1%} failed ({numbers['failure']}/{finished})")
    return alerts


def handler(event: dict, context: object) -> dict:
    records = event["Records"]
    events = [json.loads(record["body"]) for record in records]

    key = object_key([record["messageId"] for record in records], datetime.now(UTC))
    body = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in events)
    s3.put_object(Bucket=os.environ["DATA_BUCKET"], Key=key, Body=body.encode(), ContentType="application/x-ndjson")

    counts = count_finished(events)
    alerts = find_alerts(
        counts,
        threshold=float(os.environ.get("ALERT_THRESHOLD", "0.10")),
        minimum_sample=int(os.environ.get("MINIMUM_SAMPLE", "20")),
    )
    if alerts:
        sns.publish(
            TopicArn=os.environ["ALERT_TOPIC_ARN"],
            Subject="Lab 8: payment failure rate above threshold",
            Message="\n".join(alerts),
        )

    # One line per batch: the evidence that messages went through this function.
    print(json.dumps({"batch": len(records), "s3_key": key, "finished": counts, "alerts": alerts}))
    return {"written": len(records), "alerts": alerts}
