"""The API Lambda's direct-invoke seed action, and that HTTP can never reach it."""

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.lambda_handler as api
import app.reports.seed as seed_module
from app.models import Item, Meeting, MeetingParticipant
from app.reports.seed import DEMO_DOMAIN, DEMO_PREFIX
from app.reports.weekly import IsoWeek

KYIV = ZoneInfo("Europe/Kyiv")
SEED = {"action": "seed_demo_meetings", "week": "2031-W10", "confirm": True}


def function_url_event(path: str, method: str = "GET", body: str | None = None) -> dict:
    host = "abc.lambda-url.us-east-1.on.aws"
    return {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": path,
        "rawQueryString": "",
        "headers": {"host": host, "content-type": "application/json"},
        "requestContext": {
            "http": {"method": method, "path": path, "protocol": "HTTP/1.1", "sourceIp": "1.2.3.4"},
            "domainName": host,
            "stage": "$default",
            "requestId": "test",
        },
        "body": body,
        "isBase64Encoded": False,
    }


@pytest.fixture
def no_seeding(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Fail the test if anything reaches the database write."""
    calls: list[str] = []

    def forbidden(week: str) -> int:
        calls.append(week)
        raise AssertionError("seed_demo_week must not be called")

    monkeypatch.setattr(seed_module, "seed_demo_week", forbidden)
    return calls


# --- the guard rails -------------------------------------------------------------


@pytest.mark.parametrize(
    "event",
    [
        {"action": "seed_demo_meetings", "week": "2031-W10"},
        {"action": "seed_demo_meetings", "week": "2031-W10", "confirm": False},
        {"action": "seed_demo_meetings", "week": "2031-W10", "confirm": "true"},
        {"action": "seed_demo_meetings", "week": "2031-W10", "confirm": 1},
    ],
)
def test_seed_requires_confirm_true(no_seeding: list[str], event: dict) -> None:
    with pytest.raises(ValueError, match="confirm"):
        api.handler(event, None)
    assert no_seeding == []


@pytest.mark.parametrize(
    "week", [None, 202610, "2026-40", "2026-W54", "2025-W53", "2026-W41; DROP TABLE items"]
)
def test_seed_requires_a_valid_week(no_seeding: list[str], week: Any) -> None:
    event = {"action": "seed_demo_meetings", "confirm": True}
    if week is not None:
        event["week"] = week
    with pytest.raises(ValueError):
        api.handler(event, None)
    assert no_seeding == []


def test_an_http_event_carrying_the_action_goes_to_the_app(
    no_seeding: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even with the action at the top level, an HTTP-shaped event is a request."""
    sentinel = {"statusCode": 418}
    monkeypatch.setattr(api, "_asgi", lambda event, context: sentinel)
    event = {**function_url_event("/health"), **SEED}
    assert api.handler(event, None) is sentinel
    assert no_seeding == []


@pytest.mark.parametrize("key", sorted(api._HTTP_KEYS))
def test_any_http_key_disables_actions(
    no_seeding: list[str], monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    monkeypatch.setattr(api, "_asgi", lambda event, context: {"statusCode": 418})
    assert api.handler({**SEED, key: "x"}, None) == {"statusCode": 418}
    assert no_seeding == []


@pytest.mark.parametrize("path", ["/", "/api/v1/seed_demo_meetings", "/api/v1/items"])
def test_posting_the_action_over_http_never_seeds(no_seeding: list[str], path: str) -> None:
    """A real function URL request whose JSON body is the action: the app answers it."""
    response = api.handler(function_url_event(path, "POST", json.dumps(SEED)), None)
    assert response["statusCode"] in (404, 405, 422)
    assert no_seeding == []


def test_existing_migrate_action_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, "_migrate", lambda: {"status": "ok", "migrated_to": "head"})
    assert api.handler({"action": "migrate"}, None) == {"status": "ok", "migrated_to": "head"}


# --- the real thing, against the test database ----------------------------------------


async def test_seed_action_writes_only_demo_rows_of_the_two_weeks(engine) -> None:
    """Items, real meetings and demo rows of other weeks survive; re-seeding replaces."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    w10_start, _ = IsoWeek.parse("2031-W10").bounds(KYIV)
    w20_start, _ = IsoWeek.parse("2031-W20").bounds(KYIV)

    async with factory() as session:
        item = Item(name="must survive", status="todo")
        real = Meeting(
            title="Real planning",  # not a demo row: no [demo] prefix
            organiser="someone@peach.test",
            starts_at=w10_start + timedelta(days=1, hours=9),
            ends_at=w10_start + timedelta(days=1, hours=10),
        )
        other_week_demo = Meeting(
            title=DEMO_PREFIX + "Another week",
            organiser="organiser" + DEMO_DOMAIN,
            starts_at=w20_start + timedelta(hours=9),
            ends_at=w20_start + timedelta(hours=10),
        )
        session.add_all([item, real, other_week_demo])
        await session.commit()
        kept_ids = {item.id, real.id, other_week_demo.id}

    try:
        first = await asyncio.to_thread(api.handler, dict(SEED), None)
        second = await asyncio.to_thread(api.handler, dict(SEED), None)
        assert first == {
            "status": "ok",
            "action": "seed_demo_meetings",
            "weeks": ["2031-W09", "2031-W10"],
            "seeded": 10,
        }
        assert second == first

        async with factory() as session:
            start, _ = IsoWeek.parse("2031-W09").bounds(KYIV)
            _, end = IsoWeek.parse("2031-W10").bounds(KYIV)
            demo_in_range = await session.scalar(
                select(func.count())
                .select_from(Meeting)
                .where(
                    Meeting.title.startswith(DEMO_PREFIX),
                    Meeting.starts_at >= start,
                    Meeting.starts_at < end,
                )
            )
            assert demo_in_range == 10  # replaced on the second run, not doubled
            assert await session.get(Item, item.id) is not None
            assert (await session.get(Meeting, real.id)).title == "Real planning"
            assert await session.get(Meeting, other_week_demo.id) is not None
            participants = await session.scalar(
                select(func.count())
                .select_from(MeetingParticipant)
                .join(Meeting, Meeting.id == MeetingParticipant.meeting_id)
                .where(Meeting.title.startswith(DEMO_PREFIX))
            )
            assert participants == 47  # 31 this week + 16 the week before
    finally:
        async with factory() as session:
            await session.execute(
                delete(Meeting).where(
                    Meeting.title.startswith(DEMO_PREFIX) | Meeting.id.in_(kept_ids)
                )
            )
            await session.execute(delete(Item).where(Item.id == item.id))
            await session.commit()


def test_seed_action_logs_no_connection_details(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(seed_module, "seed_demo_week", lambda week: 10)
    caplog.set_level("INFO")
    result = api.handler(dict(SEED), None)
    assert result["seeded"] == 10
    text = caplog.text + json.dumps(result)
    assert "postgresql" not in text and "DATABASE_URL" not in text


def test_kyiv_week_bounds_used_by_the_test() -> None:
    start, _ = IsoWeek.parse("2031-W10").bounds(KYIV)
    assert start == datetime(2031, 3, 3, tzinfo=KYIV)
