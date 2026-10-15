"""Builder and mailer Lambda handlers, with fake S3 and SES clients - no AWS calls."""

import io
import json
import logging
import subprocess
import sys
from datetime import UTC, datetime
from email import message_from_bytes, policy
from pathlib import Path
from typing import Any

import pytest

from app.reports import lambda_handler as builder
from app.reports import mailer

CSV = b"Weekly meetings report\r\nweek,2026-W40\r\n"


class FakeS3:
    def __init__(self, objects: dict[tuple[str, str], bytes] | None = None) -> None:
        self.objects = objects or {}
        self.puts: list[dict[str, Any]] = []
        self.gets: list[tuple[str, str]] = []
        self.fail_put = False

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        if self.fail_put:
            raise RuntimeError("S3 is down")
        self.puts.append(kwargs)
        return {"ETag": '"etag"'}

    def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803 - boto3's names
        self.gets.append((Bucket, Key))
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}


class FakeSES:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.fail = False

    def send_email(self, **kwargs: Any) -> dict[str, Any]:
        if self.fail:
            raise RuntimeError("SES rejected the message")
        self.sent.append(kwargs)
        return {"MessageId": f"msg-{len(self.sent)}"}


# --- builder -------------------------------------------------------------------


@pytest.fixture
def s3(monkeypatch: pytest.MonkeyPatch) -> FakeS3:
    fake = FakeS3()
    monkeypatch.setenv("REPORTS_BUCKET", "reports-bucket")
    monkeypatch.delenv(builder.SIMULATE_FAILURE_ENV, raising=False)
    monkeypatch.setattr(builder, "s3_client", lambda: fake)
    # Monday 5 October 2026, 07:00 in Kyiv: the scheduled run.
    monkeypatch.setattr(builder, "_now", lambda: datetime(2026, 10, 5, 4, 0, tzinfo=UTC))
    return fake


@pytest.fixture
def built(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stand in for the database: record which weeks were built."""
    weeks: list[str] = []

    def fake_build(week: str) -> bytes:
        weeks.append(week)
        return CSV + week.encode()

    monkeypatch.setattr(builder, "build_weekly_report", fake_build)
    return weeks


def sqs_event(*bodies: Any) -> dict[str, Any]:
    return {
        "Records": [
            {
                "eventSource": "aws:sqs",
                "messageId": f"m{n}",
                "body": body if isinstance(body, str) else json.dumps(body),
            }
            for n, body in enumerate(bodies, start=1)
        ]
    }


def logged(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    return [json.loads(r.getMessage()) for r in caplog.records if r.getMessage().startswith("{")]


def test_direct_schedule_event_reports_the_last_complete_week(
    s3: FakeS3, built: list[str], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    result = builder.handler({"source": "schedule"}, None)

    assert built == ["2026-W40"]
    assert result["reports"] == [
        {
            "week": "2026-W40",
            "bucket": "reports-bucket",
            "key": "reports/2026-W40.csv",
            "bytes": len(CSV) + 8,
        }
    ]
    put = s3.puts[0]
    assert put["Bucket"] == "reports-bucket"
    assert put["Key"] == "reports/2026-W40.csv"
    assert put["Body"] == CSV + b"2026-W40"
    assert put["ContentType"] == "text/csv; charset=utf-8"
    assert put["Metadata"] == {"week": "2026-W40", "source": "schedule", "trigger": "direct"}
    done = [line for line in logged(caplog) if line["event"] == "report_uploaded"][0]
    assert done["trigger"] == "direct"
    assert done["source"] == "schedule"
    assert done["week"] == "2026-W40"


def test_direct_event_with_an_explicit_week(s3: FakeS3, built: list[str]) -> None:
    builder.handler({"source": "schedule", "week": "2025-W52"}, None)
    assert built == ["2025-W52"]
    assert s3.puts[0]["Key"] == "reports/2025-W52.csv"


def test_sqs_manual_request(s3: FakeS3, built: list[str], caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    builder.handler(sqs_event({"week": "2026-W40", "source": "manual"}), None)

    assert built == ["2026-W40"]
    assert s3.puts[0]["Key"] == "reports/2026-W40.csv"
    assert s3.puts[0]["Metadata"]["trigger"] == "sqs"
    done = [line for line in logged(caplog) if line["event"] == "report_uploaded"][0]
    assert (done["trigger"], done["source"], done["week"], done["message_id"]) == (
        "sqs",
        "manual",
        "2026-W40",
        "m1",
    )


def test_sqs_schedule_message_without_a_week(s3: FakeS3, built: list[str]) -> None:
    builder.handler(sqs_event({"source": "schedule"}), None)
    assert built == ["2026-W40"]
    assert s3.puts[0]["Metadata"]["source"] == "schedule"


def test_sqs_batch_builds_each_message(s3: FakeS3, built: list[str]) -> None:
    builder.handler(sqs_event({"week": "2026-W39"}, {"week": "2026-W40"}), None)
    assert [p["Key"] for p in s3.puts] == ["reports/2026-W39.csv", "reports/2026-W40.csv"]
    assert s3.puts[0]["Metadata"]["source"] == "unknown"


def test_the_same_week_always_gets_the_same_key(s3: FakeS3, built: list[str]) -> None:
    builder.handler(sqs_event({"week": "2026-W40", "source": "manual"}), None)
    builder.handler({"source": "schedule", "week": "2026-W40"}, None)
    assert [p["Key"] for p in s3.puts] == ["reports/2026-W40.csv"] * 2
    assert builder.report_key("2026-W40") == "reports/2026-W40.csv"


@pytest.mark.parametrize(
    "event",
    [
        {},
        {"source": "manual"},
        {"Records": []},
        {"Records": "nope"},
        sqs_event("not json"),
        sqs_event('["a", "list"]'),
        sqs_event({"week": "2026-W99"}),
        sqs_event({"week": 40}),
        {"Records": [{"eventSource": "aws:s3", "body": "{}"}]},
        {"Records": [{"eventSource": "aws:sqs", "messageId": "m1"}]},
    ],
)
def test_malformed_events_raise_before_uploading(
    s3: FakeS3, built: list[str], event: dict[str, Any]
) -> None:
    with pytest.raises(ValueError):
        builder.handler(event, None)
    assert s3.puts == []


def test_database_failure_propagates_for_sqs_retry(
    s3: FakeS3, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(week: str) -> bytes:
        raise ConnectionError("database unreachable")

    monkeypatch.setattr(builder, "build_weekly_report", broken)
    with pytest.raises(ConnectionError):
        builder.handler(sqs_event({"week": "2026-W40"}), None)
    assert s3.puts == []


def test_upload_failure_propagates_for_sqs_retry(s3: FakeS3, built: list[str]) -> None:
    s3.fail_put = True
    with pytest.raises(RuntimeError, match="S3 is down"):
        builder.handler(sqs_event({"week": "2026-W40"}), None)


def test_missing_bucket_setting_fails(
    s3: FakeS3, built: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("REPORTS_BUCKET")
    with pytest.raises(RuntimeError, match="REPORTS_BUCKET"):
        builder.handler({"source": "schedule"}, None)
    assert built == []


# --- test-only failure switch (DLQ redrive demonstration) -------------------------


@pytest.mark.parametrize("value", ["true", "TRUE", " true "])
def test_failure_switch_fails_valid_requests_before_any_access(
    s3: FakeS3,
    built: list[str],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    value: str,
) -> None:
    monkeypatch.setenv(builder.SIMULATE_FAILURE_ENV, value)
    caplog.set_level(logging.INFO)
    with pytest.raises(RuntimeError, match="simulated builder outage"):
        builder.handler(sqs_event({"week": "2026-W39", "source": "manual"}), None)

    assert built == []  # no database read
    assert s3.puts == []  # no report, so no email
    lines = logged(caplog)
    failed = [line for line in lines if line["event"] == "simulated_failure"]
    assert [(f["trigger"], f["source"], f["week"]) for f in failed] == [
        ("sqs", "manual", "2026-W39")
    ]
    assert not any(line["event"] == "build_started" for line in lines)


def test_failure_switch_applies_to_scheduled_runs_too(
    s3: FakeS3, built: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(builder.SIMULATE_FAILURE_ENV, "true")
    with pytest.raises(RuntimeError, match="simulated builder outage"):
        builder.handler({"source": "schedule"}, None)
    assert built == [] and s3.puts == []


@pytest.mark.parametrize("value", ["", "false", "False", "1", "yes", "on", "t", "truthy"])
def test_failure_switch_is_off_unless_exactly_true(
    s3: FakeS3, built: list[str], monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(builder.SIMULATE_FAILURE_ENV, value)
    builder.handler({"source": "schedule", "week": "2026-W40"}, None)
    assert built == ["2026-W40"]
    assert [p["Key"] for p in s3.puts] == ["reports/2026-W40.csv"]


def test_failure_switch_is_off_when_unset(s3: FakeS3, built: list[str]) -> None:
    assert not builder.simulated_failure_enabled()
    builder.handler(sqs_event({"week": "2026-W40", "source": "manual"}), None)
    assert built == ["2026-W40"]


def test_week_validation_still_comes_first_with_the_switch_on(
    s3: FakeS3, built: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bad week is still a ValueError (a poison message), never the simulated outage."""
    monkeypatch.setenv(builder.SIMULATE_FAILURE_ENV, "true")
    with pytest.raises(ValueError, match="week must look like"):
        builder.handler(sqs_event({"week": "not-a-week", "source": "dlq-test"}), None)
    assert built == [] and s3.puts == []


# --- mailer ----------------------------------------------------------------------


@pytest.fixture
def mail(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeS3, FakeSES]:
    s3 = FakeS3({("reports-bucket", "reports/2026-W40.csv"): CSV})
    ses = FakeSES()

    def no_writes(**kwargs: Any) -> None:
        raise AssertionError("the mailer must never write to S3")

    s3.put_object = no_writes  # type: ignore[method-assign]
    monkeypatch.setattr(mailer, "s3_client", lambda: s3)
    monkeypatch.setattr(mailer, "ses_client", lambda: ses)
    monkeypatch.setenv("REPORT_SENDER", "reports@example.com")
    monkeypatch.setenv("REPORT_RECIPIENTS", "a@example.com, b@example.com")
    return s3, ses


def s3_event(key: str, bucket: str = "reports-bucket", name: str = "ObjectCreated:Put") -> dict:
    return {
        "Records": [
            {
                "eventSource": "aws:s3",
                "eventName": name,
                "s3": {"bucket": {"name": bucket}, "object": {"key": key, "size": 10}},
            }
        ]
    }


def test_mailer_sends_the_csv_as_an_attachment(
    mail: tuple[FakeS3, FakeSES], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    s3, ses = mail
    result = mailer.handler(s3_event("reports/2026-W40.csv"), None)

    assert s3.gets == [("reports-bucket", "reports/2026-W40.csv")]
    assert result["sent"] == [{"key": "reports/2026-W40.csv", "message_id": "msg-1"}]
    call = ses.sent[0]
    assert call["FromEmailAddress"] == "reports@example.com"
    assert call["Destination"] == {"ToAddresses": ["a@example.com", "b@example.com"]}

    email = message_from_bytes(call["Content"]["Raw"]["Data"], policy=policy.default)
    assert email["Subject"] == "Peach weekly meetings report - 2026-W40"
    assert email["From"] == "reports@example.com"
    assert email["To"] == "a@example.com, b@example.com"
    assert "2026-W40" in email.get_body(preferencelist=("plain",)).get_content()
    attachments = list(email.iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_content_type() == "text/csv"
    assert attachments[0].get_filename() == "peach-meetings-2026-W40.csv"
    assert attachments[0].get_payload(decode=True) == CSV

    sent = [line for line in logged(caplog) if line["event"] == "mail_sent"][0]
    assert (sent["trigger"], sent["key"], sent["recipients"]) == ("s3", "reports/2026-W40.csv", 2)


def test_mailer_url_decodes_the_object_key(mail: tuple[FakeS3, FakeSES]) -> None:
    s3, ses = mail
    s3.objects[("reports-bucket", "reports/2026-W40 (rerun).csv")] = CSV
    # S3 sends a space as "+" and parentheses percent-encoded.
    mailer.handler(s3_event("reports/2026-W40+%28rerun%29.csv"), None)
    assert s3.gets == [("reports-bucket", "reports/2026-W40 (rerun).csv")]
    assert len(ses.sent) == 1


def test_mailer_ignores_objects_outside_reports(mail: tuple[FakeS3, FakeSES]) -> None:
    s3, ses = mail
    result = mailer.handler(s3_event("other/notes.txt"), None)
    assert result["sent"] == []
    assert s3.gets == []
    assert ses.sent == []


@pytest.mark.parametrize(
    "event",
    [
        {},
        {"Records": []},
        "not an event",
        s3_event("reports/2026-W40.csv", name="ObjectRemoved:Delete"),
        {"Records": [{"eventSource": "aws:sqs", "body": "{}"}]},
        {"Records": [{"eventSource": "aws:s3", "eventName": "ObjectCreated:Put", "s3": {}}]},
    ],
)
def test_mailer_rejects_malformed_events(mail: tuple[FakeS3, FakeSES], event: Any) -> None:
    _, ses = mail
    with pytest.raises(ValueError):
        mailer.handler(event, None)
    assert ses.sent == []


def test_mailer_failures_propagate(mail: tuple[FakeS3, FakeSES]) -> None:
    _, ses = mail
    ses.fail = True
    with pytest.raises(RuntimeError, match="SES rejected"):
        mailer.handler(s3_event("reports/2026-W40.csv"), None)
    with pytest.raises(KeyError):  # the object is gone
        mailer.handler(s3_event("reports/2026-W41.csv"), None)


@pytest.mark.parametrize("missing", ["REPORT_SENDER", "REPORT_RECIPIENTS"])
def test_mailer_requires_sender_and_recipients(
    mail: tuple[FakeS3, FakeSES], monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    monkeypatch.delenv(missing)
    with pytest.raises(RuntimeError, match=missing):
        mailer.handler(s3_event("reports/2026-W40.csv"), None)


def test_mailer_is_self_contained_for_a_zip_deploy() -> None:
    """Imported on its own, like a zip with handler mailer.handler: no app, no database."""
    reports_dir = Path(mailer.__file__).parent
    check = (
        "import sys; sys.path.insert(0, sys.argv[1]); import mailer; "
        "bad = [m for m in sys.modules if m == 'app' or m.startswith(('app.', 'sqlalchemy'))]; "
        "assert not bad, bad; print('ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", check, str(reports_dir)], capture_output=True, text=True
    )
    assert result.stdout.strip() == "ok", result.stderr
