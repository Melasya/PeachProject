import asyncio
import csv
import io
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Meeting, MeetingParticipant
from app.reports.weekly import (
    IsoWeek,
    build_weekly_report,
    generate_weekly_report,
    last_complete_week,
)

KYIV = ZoneInfo("Europe/Kyiv")
UTC = ZoneInfo("UTC")


def kyiv(*args: int) -> datetime:
    return datetime(*args, tzinfo=KYIV)


async def add_meeting(
    session: AsyncSession, title: str, start: datetime, minutes: int, participants: int = 0
) -> Meeting:
    meeting = Meeting(
        title=title,
        organiser="organiser@example.com",
        starts_at=start,
        ends_at=start + timedelta(minutes=minutes),
    )
    session.add(meeting)
    await session.flush()
    session.add_all(
        MeetingParticipant(meeting_id=meeting.id, email=f"p{n}@example.com")
        for n in range(participants)
    )
    await session.flush()
    return meeting


def parse(report: bytes) -> dict:
    """Split the CSV into its labelled sections."""
    sections: list[list[list[str]]] = [[]]
    for row in csv.reader(io.StringIO(report.decode("utf-8"))):
        if row:
            sections[-1].append(row)
        else:
            sections.append([])
    header, summary, top = sections
    assert header[0] == ["Weekly meetings report"]
    assert summary[0] == ["Summary"]
    assert summary[1] == ["metric", "current_week", "previous_week", "difference"]
    assert top[0] == ["Top 5 longest meetings"]
    assert top[1] == ["rank", "title", "start", "duration_hours", "participants"]
    return {
        "info": dict(header[1:]),
        "meetings": summary[2][1:],
        "hours": summary[3][1:],
        "top": top[2:],
    }


async def report(session: AsyncSession, week: str) -> dict:
    return parse(await generate_weekly_report(session, week, timezone="Europe/Kyiv"))


# --- ISO weeks ---------------------------------------------------------------


def test_parse_and_format() -> None:
    assert IsoWeek.parse("2026-W40") == IsoWeek(2026, 40)
    assert str(IsoWeek.parse(" 2026-W01 ")) == "2026-W01"


@pytest.mark.parametrize(
    "bad", ["2026-40", "2026-w40", "2026-W4", "2026-W00", "2026-W54", "2025-W53", "26-W01", ""]
)
def test_parse_rejects_malformed_and_missing_weeks(bad: str) -> None:
    with pytest.raises(ValueError):
        IsoWeek.parse(bad)


def test_week_53_exists_only_in_long_years() -> None:
    assert IsoWeek.parse("2020-W53").monday == date(2020, 12, 28)
    with pytest.raises(ValueError):
        IsoWeek.parse("2025-W53")


def test_bounds_are_monday_midnight_in_kyiv() -> None:
    start, end = IsoWeek.parse("2026-W40").bounds(KYIV)
    assert start == kyiv(2026, 9, 28)
    assert end == kyiv(2026, 10, 5)
    assert start.utcoffset() == timedelta(hours=3)
    assert start.astimezone(UTC) == datetime(2026, 9, 27, 21, 0, tzinfo=UTC)


def test_week_that_ends_daylight_saving_is_an_hour_longer() -> None:
    # Kyiv leaves summer time on Sunday 25 October 2026.
    start, end = IsoWeek.parse("2026-W43").bounds(KYIV)
    assert start.utcoffset() == timedelta(hours=3)
    assert end.utcoffset() == timedelta(hours=2)
    assert end.astimezone(UTC) - start.astimezone(UTC) == timedelta(days=7, hours=1)


def test_previous_week_crosses_the_year_boundary() -> None:
    assert IsoWeek.parse("2026-W40").previous() == IsoWeek(2026, 39)
    assert IsoWeek.parse("2026-W01").previous() == IsoWeek(2025, 52)
    assert IsoWeek.parse("2021-W01").previous() == IsoWeek(2020, 53)
    # ISO week 1 of 2026 starts in December 2025.
    assert IsoWeek.parse("2026-W01").monday == date(2025, 12, 29)


def test_last_complete_week_for_a_monday_run() -> None:
    # The schedule fires on Monday 07:00 Kyiv and reports the week just ended.
    assert last_complete_week(kyiv(2026, 10, 5, 7), KYIV) == "2026-W40"
    assert last_complete_week(kyiv(2026, 1, 5, 7), KYIV) == "2026-W01"
    assert last_complete_week(kyiv(2026, 1, 1, 12), KYIV) == "2025-W52"
    # 22:30 UTC on Sunday is already Monday 01:30 in Kyiv.
    assert last_complete_week(datetime(2026, 10, 4, 22, 30, tzinfo=UTC), KYIV) == "2026-W40"


# --- the report ----------------------------------------------------------------


async def test_empty_week(session: AsyncSession) -> None:
    r = await report(session, "2026-W40")
    assert r["info"]["week"] == "2026-W40"
    assert r["info"]["previous_week"] == "2026-W39"
    assert r["info"]["timezone"] == "Europe/Kyiv"
    assert r["info"]["period_start"] == "2026-09-28T00:00+03:00"
    assert r["info"]["period_end"] == "2026-10-05T00:00+03:00"
    assert r["meetings"] == ["0", "0", "+0"]
    assert r["hours"] == ["0.00", "0.00", "+0.00"]
    assert r["top"] == []


async def test_meetings_fall_in_the_week_they_start_in_kyiv(session: AsyncSession) -> None:
    await add_meeting(session, "first minute of W40", kyiv(2026, 9, 28, 0, 0), 30)
    await add_meeting(session, "last minutes of W40", kyiv(2026, 10, 4, 23, 30), 60)
    await add_meeting(session, "last second of W39", kyiv(2026, 9, 27, 23, 59, 59), 30)
    await add_meeting(session, "first minute of W41", kyiv(2026, 10, 5, 0, 0), 30)
    # Sunday 21:30 UTC is Monday 00:30 in Kyiv: week 41, not 40.
    await add_meeting(session, "entered in UTC", datetime(2026, 10, 4, 21, 30, tzinfo=UTC), 30)

    r = await report(session, "2026-W40")
    assert r["meetings"] == ["2", "1", "+1"]
    titles = {row[1] for row in r["top"]}
    assert titles == {"first minute of W40", "last minutes of W40"}
    # The meeting that runs past midnight on Sunday counts its full hour.
    assert r["hours"][0] == "1.50"


async def test_counts_hours_and_differences(session: AsyncSession) -> None:
    await add_meeting(session, "a", kyiv(2026, 9, 29, 10), 90)
    await add_meeting(session, "b", kyiv(2026, 9, 30, 10), 60)
    await add_meeting(session, "c", kyiv(2026, 10, 1, 10), 30)
    await add_meeting(session, "last week", kyiv(2026, 9, 23, 10), 45)

    r = await report(session, "2026-W40")
    assert r["meetings"] == ["3", "1", "+2"]
    assert r["hours"] == ["3.00", "0.75", "+2.25"]


async def test_fewer_meetings_than_last_week_gives_negative_differences(
    session: AsyncSession,
) -> None:
    await add_meeting(session, "this week", kyiv(2026, 9, 29, 10), 30)
    await add_meeting(session, "last week 1", kyiv(2026, 9, 22, 10), 30)
    await add_meeting(session, "last week 2", kyiv(2026, 9, 24, 10), 30)

    r = await report(session, "2026-W40")
    assert r["meetings"] == ["1", "2", "-1"]
    assert r["hours"] == ["0.50", "1.00", "-0.50"]


async def test_hours_are_rounded_to_hundredths(session: AsyncSession) -> None:
    await add_meeting(session, "twenty minutes", kyiv(2026, 9, 29, 10), 20)
    r = await report(session, "2026-W40")
    assert r["hours"][0] == "0.33"


async def test_comparison_across_the_new_year(session: AsyncSession) -> None:
    await add_meeting(session, "new year", kyiv(2026, 1, 2, 10), 60)  # 2026-W01
    await add_meeting(session, "old year", kyiv(2025, 12, 24, 10), 120)  # 2025-W52

    r = await report(session, "2026-W01")
    assert r["info"]["previous_week"] == "2025-W52"
    assert r["info"]["period_start"] == "2025-12-29T00:00+02:00"
    assert r["meetings"] == ["1", "1", "+0"]
    assert r["hours"] == ["1.00", "2.00", "-1.00"]


async def test_top_five_longest_meetings(session: AsyncSession) -> None:
    durations = {"m15": 15, "m200": 200, "m45": 45, "m120": 120, "m90": 90, "m60": 60}
    monday = kyiv(2026, 9, 28, 9)
    for day, (title, minutes) in enumerate(durations.items()):
        start = monday + timedelta(days=day)
        await add_meeting(session, title, start, minutes, participants=day)
    # A tie with m90, but starting later, so it ranks after it.
    await add_meeting(session, "m90 later", kyiv(2026, 10, 4, 18), 90, participants=2)
    # Longer than all of them, but in another week.
    await add_meeting(session, "other week", kyiv(2026, 9, 22, 9), 600)

    r = await report(session, "2026-W40")
    assert r["meetings"][0] == "7"
    assert [row[0] for row in r["top"]] == ["1", "2", "3", "4", "5"]
    assert [row[1] for row in r["top"]] == ["m200", "m120", "m90", "m90 later", "m60"]
    assert [row[3] for row in r["top"]] == ["3.33", "2.00", "1.50", "1.50", "1.00"]
    assert [row[4] for row in r["top"]] == ["1", "3", "4", "2", "5"]
    assert r["top"][0][2] == "2026-09-29T09:00+03:00"


async def test_titles_cannot_inject_spreadsheet_formulas(session: AsyncSession) -> None:
    await add_meeting(session, "=HYPERLINK(1)", kyiv(2026, 9, 29, 10), 60)
    await add_meeting(session, "Plan, then ship", kyiv(2026, 9, 30, 10), 30)

    r = await report(session, "2026-W40")
    assert [row[1] for row in r["top"]] == ["'=HYPERLINK(1)", "Plan, then ship"]


async def test_build_weekly_report_reads_committed_rows(engine) -> None:
    """The synchronous Lambda entry point, against rows another connection committed."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await add_meeting(session, "sync check", kyiv(2031, 3, 4, 10), 75, participants=3)
        await session.commit()
    try:
        # asyncio.run inside it needs a thread without a running loop.
        csv_bytes = await asyncio.to_thread(build_weekly_report, "2031-W10")
        r = parse(csv_bytes)
        assert r["meetings"] == ["1", "0", "+1"]
        assert r["top"][0][1:] == ["sync check", "2031-03-04T10:00+02:00", "1.25", "3"]
    finally:
        async with factory() as session:
            await session.execute(delete(Meeting).where(Meeting.title == "sync check"))
            await session.commit()


def test_build_weekly_report_rejects_a_bad_week_before_connecting() -> None:
    with pytest.raises(ValueError):
        build_weekly_report("2026-W99")
