"""Mailer Lambda: email a new weekly report CSV through Amazon SES.

Triggered directly by the reports bucket's S3 ObjectCreated notifications for
``reports/*.csv`` - no queue in between. It runs outside the VPC, never touches
the database and only reads from S3: it never writes under ``reports/``, so it
cannot trigger itself.

This file is self-contained on purpose - the standard library and boto3, which
the Lambda Python runtime already includes - so it deploys as a small zip with
handler ``mailer.handler``, independently of the backend container image. Do
not import anything from ``app`` here.

Configuration (environment):
- REPORT_SENDER     verified SES identity the email comes from
- REPORT_RECIPIENTS comma-separated addresses (verified too, in the SES sandbox)
"""

import json
import logging
import os
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Any
from urllib.parse import unquote_plus

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

REPORT_PREFIX = "reports/"

_clients: dict[str, Any] = {}


def s3_client() -> Any:
    if "s3" not in _clients:
        _clients["s3"] = boto3.client("s3")
    return _clients["s3"]


def ses_client() -> Any:
    if "ses" not in _clients:
        _clients["ses"] = boto3.client("sesv2")
    return _clients["ses"]


def _log(**fields: Any) -> None:
    logger.info(json.dumps({"component": "report-mailer", **fields}, default=str))


def _config() -> tuple[str, list[str]]:
    sender = os.environ.get("REPORT_SENDER", "").strip()
    recipients = [
        address.strip()
        for address in os.environ.get("REPORT_RECIPIENTS", "").split(",")
        if address.strip()
    ]
    if not sender:
        raise RuntimeError("REPORT_SENDER is not set")
    if not recipients:
        raise RuntimeError("REPORT_RECIPIENTS is not set")
    return sender, recipients


def parse_s3_record(record: dict[str, Any]) -> tuple[str, str]:
    """(bucket, key) from one S3 notification record, with the key URL-decoded.

    S3 encodes keys in notifications the way it encodes form values: a space
    arrives as "+" and other characters percent-encoded, hence unquote_plus.
    """
    if record.get("eventSource") != "aws:s3":
        raise ValueError(f"unexpected record source {record.get('eventSource')!r}")
    if not str(record.get("eventName", "")).startswith("ObjectCreated:"):
        raise ValueError(f"unexpected S3 event {record.get('eventName')!r}")
    try:
        bucket = record["s3"]["bucket"]["name"]
        key = unquote_plus(record["s3"]["object"]["key"])
    except (KeyError, TypeError) as error:
        raise ValueError("S3 record has no bucket name or object key") from error
    return bucket, key


def week_of(key: str) -> str:
    """reports/2026-W40.csv -> 2026-W40."""
    return key.rsplit("/", 1)[-1].removesuffix(".csv")


def build_message(sender: str, recipients: list[str], key: str, csv_bytes: bytes) -> EmailMessage:
    week = week_of(key)
    message = EmailMessage()
    message["Subject"] = f"Peach weekly meetings report - {week}"
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid(domain="peach.reports")
    message.set_content(
        f"The weekly meetings report for {week} is attached as a CSV file.\n\n"
        "It compares the week with the one before it and lists the five longest "
        "meetings.\n\n- Peach\n"
    )
    message.add_attachment(
        csv_bytes,
        maintype="text",
        subtype="csv",
        filename=f"peach-meetings-{week}.csv",
    )
    return message


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    records = event.get("Records") if isinstance(event, dict) else None
    if not isinstance(records, list) or not records:
        raise ValueError("expected an S3 event with Records")
    sender, recipients = _config()

    sent = []
    for record in records:
        bucket, key = parse_s3_record(record)
        if not (key.startswith(REPORT_PREFIX) and key.endswith(".csv")):
            # The notification filter should prevent this; never mail other objects.
            _log(event="skipped", trigger="s3", bucket=bucket, key=key)
            continue
        _log(event="mail_started", trigger="s3", bucket=bucket, key=key)

        csv_bytes = s3_client().get_object(Bucket=bucket, Key=key)["Body"].read()
        message = build_message(sender, recipients, key, csv_bytes)
        response = ses_client().send_email(
            FromEmailAddress=sender,
            Destination={"ToAddresses": recipients},
            Content={"Raw": {"Data": message.as_bytes()}},
        )
        message_id = response.get("MessageId")
        _log(
            event="mail_sent",
            trigger="s3",
            key=key,
            recipients=len(recipients),
            bytes=len(csv_bytes),
            ses_message_id=message_id,
        )
        sent.append({"key": key, "message_id": message_id})
    return {"status": "ok", "sent": sent}
