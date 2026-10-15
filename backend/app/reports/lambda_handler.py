"""Builder Lambda: build the weekly meetings report and put it in S3.

Runs inside the VPC (it reads RDS) from the backend image, with its handler set
to ``app.reports.lambda_handler.handler``. Two event shapes arrive here:

- SQS: ``{"Records": [{"eventSource": "aws:sqs", "body": "{...}"}]}`` from the
  report-requests queue. The scheduler sends ``{"source": "schedule"}`` there;
  ``make report-now`` sends ``{"week": "2026-W40", "source": "manual"}``. This
  is the production path.
- Direct invocation, schedule-shaped: ``{"source": "schedule"}``, optionally
  with a ``week``. Kept for testing the builder before the queue exists.

With no ``week``, it reports the last complete ISO week in REPORT_TIMEZONE
(Europe/Kyiv) - what a Monday 07:00 run should send. Each report goes to the
deterministic key ``reports/<week>.csv``, so a retry overwrites rather than
duplicates. Every failure raises: SQS then makes the message visible again,
and after maxReceiveCount attempts moves it to the dead-letter queue.
"""

import json
import logging
import os
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import boto3

from app.config import get_settings
from app.reports.weekly import IsoWeek, build_weekly_report, last_complete_week

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

KEY_PREFIX = "reports/"

# Test-only switch for the DLQ redrive demonstration: when exactly "true", every
# valid request fails after validation and before any database or S3 access, so
# messages go to the DLQ untouched. Set only by `deploy-reports.sh
# --simulate-failure`; every other deploy sets it back to "false".
SIMULATE_FAILURE_ENV = "REPORTS_SIMULATE_FAILURE"

_s3: Any = None


def s3_client() -> Any:
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def _now() -> datetime:
    return datetime.now(UTC)


def report_key(week: str) -> str:
    """The one S3 key for a week's report: reports/2026-W40.csv."""
    return f"{KEY_PREFIX}{IsoWeek.parse(week)}.csv"


def _log(**fields: Any) -> None:
    logger.info(json.dumps({"component": "report-builder", **fields}, default=str))


def _resolve_week(request: dict[str, Any]) -> str:
    week = request.get("week")
    if week is None:
        tz = ZoneInfo(get_settings().report_timezone)
        return last_complete_week(_now(), tz)
    if not isinstance(week, str):
        raise ValueError(f"week must be a string like 2026-W40, got {week!r}")
    return str(IsoWeek.parse(week))


def simulated_failure_enabled() -> bool:
    return os.environ.get(SIMULATE_FAILURE_ENV, "").strip().lower() == "true"


def _build_and_upload(request: dict[str, Any], trigger: str, **context: Any) -> dict[str, Any]:
    bucket = os.environ.get("REPORTS_BUCKET")
    if not bucket:
        raise RuntimeError("REPORTS_BUCKET is not set")
    source = request.get("source", "unknown")
    week = _resolve_week(request)  # validation always comes first
    key = report_key(week)
    if simulated_failure_enabled():
        _log(event="simulated_failure", trigger=trigger, source=source, week=week, **context)
        raise RuntimeError(f"simulated builder outage ({SIMULATE_FAILURE_ENV}=true) - test only")
    _log(event="build_started", trigger=trigger, source=source, week=week, **context)

    body = build_weekly_report(week)
    s3_client().put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType="text/csv; charset=utf-8",
        Metadata={"week": week, "source": str(source), "trigger": trigger},
    )
    _log(
        event="report_uploaded",
        trigger=trigger,
        source=source,
        week=week,
        key=key,
        bytes=len(body),
        **context,
    )
    return {"week": week, "bucket": bucket, "key": key, "bytes": len(body)}


def _from_sqs(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results = []
    for record in records:
        if record.get("eventSource") != "aws:sqs":
            raise ValueError(f"unexpected record source {record.get('eventSource')!r}")
        message_id = record.get("messageId")
        try:
            request = json.loads(record["body"])
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise ValueError(f"SQS message {message_id} has no JSON body") from error
        if not isinstance(request, dict):
            raise ValueError(f"SQS message {message_id} body must be a JSON object")
        results.append(_build_and_upload(request, trigger="sqs", message_id=message_id))
    return results


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise ValueError("event must be a JSON object")
    if "Records" in event:
        records = event["Records"]
        if not isinstance(records, list) or not records:
            raise ValueError("Records must be a non-empty list")
        # An exception here fails the batch: SQS retries it, then dead-letters it.
        reports = _from_sqs(records)
    elif event.get("source") == "schedule" or "week" in event:
        reports = [_build_and_upload(event, trigger="direct")]
    else:
        raise ValueError("expected SQS Records or a schedule-shaped event")
    return {"status": "ok", "reports": reports}
