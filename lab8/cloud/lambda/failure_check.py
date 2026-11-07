"""Kinesis -> this function -> SNS: alert when a payment method's failure rate is too high.

Each invocation judges one batch from Kinesis. Unlike the local consumer it keeps no state between
invocations, so its "window" is the batch (processing time), not an event-time minute.
"""

import base64
import json
import os

import boto3

sns = boto3.client("sns")


def failure_rates(records: list[dict]) -> dict[str, dict[str, int]]:
    """Count finished payments per method. A duplicate inside the batch is counted once."""
    seen = set()
    counts: dict[str, dict[str, int]] = {}
    for record in records:
        event = json.loads(base64.b64decode(record["kinesis"]["data"]))
        identity = (event["invoiceId"], event["status"], event["modifiedDate"])
        if identity in seen or event["status"] not in ("success", "failure"):
            continue
        seen.add(identity)
        method = counts.setdefault(event["paymentMethod"], {"success": 0, "failure": 0})
        method[event["status"]] += 1
    return counts


def handler(event: dict, context: object) -> dict:
    threshold = float(os.environ.get("ALERT_THRESHOLD", "0.10"))
    minimum_sample = int(os.environ.get("MINIMUM_SAMPLE", "20"))

    counts = failure_rates(event["Records"])
    alerts = []
    for method, numbers in sorted(counts.items()):
        finished = numbers["success"] + numbers["failure"]
        rate = numbers["failure"] / finished
        # A rate from three payments says nothing; do not page anyone over it.
        if finished >= minimum_sample and rate > threshold:
            alerts.append(f"{method}: {rate:.1%} failed ({numbers['failure']}/{finished})")

    print(json.dumps({"records": len(event["Records"]), "counts": counts, "alerts": alerts}))
    if alerts:
        sns.publish(
            TopicArn=os.environ["ALERT_TOPIC_ARN"],
            Subject="Lab 8: payment failure rate above threshold",
            Message="\n".join(alerts),
        )
    return {"alerts": alerts}
