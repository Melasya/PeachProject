"""The alert logic, tested with a fake SNS: python -m pytest test_failure_check.py"""

import base64
import json
import os
import sys
from pathlib import Path
from unittest import mock

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
sys.path.append(str(Path(__file__).parent / "lambda"))
import failure_check


def record(invoice: str, status: str, method: str = "card") -> dict:
    event = {"invoiceId": invoice, "status": status, "modifiedDate": "2026-10-15T10:00:00Z", "paymentMethod": method}
    return {"kinesis": {"data": base64.b64encode(json.dumps(event).encode() + b"\n").decode()}}


def batch(failures: int, successes: int, method: str = "card") -> list[dict]:
    return [record(f"f{i}", "failure", method) for i in range(failures)] + [
        record(f"s{i}", "success", method) for i in range(successes)
    ]


def run(records: list[dict]) -> tuple[dict, mock.Mock]:
    env = {"ALERT_TOPIC_ARN": "arn:topic", "ALERT_THRESHOLD": "0.10", "MINIMUM_SAMPLE": "20"}
    with mock.patch.dict(os.environ, env), mock.patch.object(failure_check, "sns") as sns:
        return failure_check.handler({"Records": records}, None), sns


def test_alert_fires_above_threshold():
    result, sns = run(batch(failures=6, successes=14))
    assert result["alerts"] == ["card: 30.0% failed (6/20)"]
    sns.publish.assert_called_once()
    assert "30.0%" in sns.publish.call_args.kwargs["Message"]


def test_no_alert_at_the_threshold():
    result, sns = run(batch(failures=2, successes=18))  # exactly 10% is not above 10%
    assert result["alerts"] == []
    sns.publish.assert_not_called()


def test_small_sample_does_not_alert():
    result, sns = run(batch(failures=5, successes=5))
    assert result["alerts"] == []
    sns.publish.assert_not_called()


def test_duplicates_and_unfinished_events_are_not_counted():
    records = batch(failures=1, successes=1) + [record("f0", "failure"), record("x", "created")]
    assert failure_check.failure_rates(records) == {"card": {"success": 1, "failure": 1}}


def test_methods_are_judged_separately():
    result, _ = run(batch(failures=6, successes=14, method="card") + batch(failures=0, successes=30, method="apple_pay"))
    assert result["alerts"] == ["card: 30.0% failed (6/20)"]
