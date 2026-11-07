"""The function's logic, with fake S3 and SNS clients: python -m pytest test_handler.py"""

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
sys.path.append(str(Path(__file__).parent / "lambda"))
import handler as function


def event(invoice: str, status: str, method: str = "card") -> dict:
    return {"invoiceId": invoice, "status": status, "modifiedDate": "2026-10-15T10:00:00Z", "paymentMethod": method}


def sqs_event(events: list[dict]) -> dict:
    return {"Records": [{"messageId": f"m{i}", "body": json.dumps(e)} for i, e in enumerate(events)]}


def batch(failures: int, successes: int, method: str = "card") -> list[dict]:
    return [event(f"f{i}{method}", "failure", method) for i in range(failures)] + [
        event(f"s{i}{method}", "success", method) for i in range(successes)
    ]


def run(events: list[dict]):
    env = {"DATA_BUCKET": "bucket", "ALERT_TOPIC_ARN": "arn:topic", "ALERT_THRESHOLD": "0.10", "MINIMUM_SAMPLE": "20"}
    with mock.patch.dict(os.environ, env), mock.patch.object(function, "s3") as s3, mock.patch.object(function, "sns") as sns:
        return function.handler(sqs_event(events), None), s3, sns


def test_batch_becomes_one_jsonl_object():
    _, s3, _ = run(batch(1, 1))
    written = s3.put_object.call_args.kwargs
    assert written["Bucket"] == "bucket"
    assert written["Key"].startswith("sqs-stream/dt=")
    assert [json.loads(line)["status"] for line in written["Body"].decode().splitlines()] == ["failure", "success"]


def test_retried_batch_gets_the_same_key():
    now = datetime(2026, 11, 5, tzinfo=UTC)
    assert function.object_key(["a", "b"], now) == function.object_key(["b", "a"], now)
    assert function.object_key(["a", "b"], now) != function.object_key(["a", "c"], now)


def test_alert_fires_above_threshold():
    result, _, sns = run(batch(failures=6, successes=14))
    assert result["alerts"] == ["card: 30.0% failed (6/20)"]
    sns.publish.assert_called_once()


def test_no_alert_at_exactly_the_threshold():
    result, _, sns = run(batch(failures=2, successes=18))
    assert result["alerts"] == []
    sns.publish.assert_not_called()


def test_small_sample_does_not_alert():
    result, _, sns = run(batch(failures=5, successes=5))
    assert result["alerts"] == []
    sns.publish.assert_not_called()


def test_duplicates_and_unfinished_events_are_not_counted():
    events = batch(1, 1) + [event("f0card", "failure"), event("x", "created")]
    assert function.count_finished(events) == {"card": {"success": 1, "failure": 1}}


def test_methods_are_judged_separately():
    result, _, _ = run(batch(6, 14, "card") + batch(0, 30, "apple_pay"))
    assert result["alerts"] == ["card: 30.0% failed (6/20)"]
