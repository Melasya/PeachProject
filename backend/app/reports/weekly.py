"""The weekly meetings report: one ISO week against the week before, as CSV.

A meeting belongs to the ISO week in which it starts. Weeks run from Monday
00:00 to the next Monday 00:00 in the report's time zone (``REPORT_TIMEZONE``,
Europe/Kyiv by default - the zone the schedule fires in), so a meeting at
Monday 01:00 in Kyiv is in that week even though it is still Sunday in UTC.
Daylight-saving changes are handled by the zone: the week that ends DST is an
hour longer in absolute time. A meeting's duration is ``ends_at - starts_at``,
and its participant count is its rows in ``meeting_participants``.
"""

import asyncio
import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.models import Meeting, MeetingParticipant

TOP_N = 5
_WEEK = re.compile(r"(\d{4})-W(\d{2})")
_HUNDREDTHS = Decimal("0.01")
# Spreadsheet apps run a cell that starts with one of these as a formula.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


@dataclass(frozen=True)
class IsoWeek:
    year: int
    week: int

    @classmethod
    def parse(cls, value: str) -> IsoWeek:
        """Parse "2026-W40"; reject anything else, including weeks a year lacks."""
        match = _WEEK.fullmatch(value.strip())
        if match is None:
            raise ValueError(f"week must look like 2026-W40, got {value!r}")
        year, week = int(match[1]), int(match[2])
        try:
            date.fromisocalendar(year, week, 1)
        except ValueError:
            raise ValueError(f"{value!r} is not an ISO week of {year}") from None
        return cls(year, week)

    @classmethod
    def containing(cls, moment: datetime, tz: ZoneInfo) -> IsoWeek:
        year, week, _ = moment.astimezone(tz).isocalendar()
        return cls(year, week)

    def __str__(self) -> str:
        return f"{self.year:04d}-W{self.week:02d}"

    @property
    def monday(self) -> date:
        return date.fromisocalendar(self.year, self.week, 1)

    def previous(self) -> IsoWeek:
        """The week before - across a year boundary, week 52 or 53 of the old year."""
        year, week, _ = (self.monday - timedelta(days=7)).isocalendar()
        return IsoWeek(year, week)

    def bounds(self, tz: ZoneInfo) -> tuple[datetime, datetime]:
        """[start, end): Monday 00:00 to the next Monday 00:00, local to ``tz``."""
        start = datetime.combine(self.monday, time.min, tzinfo=tz)
        end = datetime.combine(self.monday + timedelta(days=7), time.min, tzinfo=tz)
        return start, end


def last_complete_week(now: datetime, tz: ZoneInfo) -> str:
    """The week before the one containing ``now``: what a Monday-morning run reports."""
    return str(IsoWeek.containing(now, tz).previous())


@dataclass(frozen=True)
class LongMeeting:
    title: str
    starts_at: datetime
    seconds: Decimal
    participants: int


@dataclass(frozen=True)
class WeekStats:
    week: IsoWeek
    start: datetime
    end: datetime
    meetings: int
    seconds: Decimal
    longest: list[LongMeeting]

    @property
    def hours(self) -> Decimal:
        return _hours(self.seconds)


def _hours(seconds: Decimal) -> Decimal:
    return (seconds / 3600).quantize(_HUNDREDTHS, rounding=ROUND_HALF_UP)


async def collect_week(session: AsyncSession, week: IsoWeek, tz: ZoneInfo) -> WeekStats:
    start, end = week.bounds(tz)
    in_week = (Meeting.starts_at >= start) & (Meeting.starts_at < end)
    seconds = func.extract("epoch", Meeting.ends_at - Meeting.starts_at)

    count, total = (
        await session.execute(
            select(func.count(), func.coalesce(func.sum(seconds), 0)).where(in_week)
        )
    ).one()

    participants = (
        select(func.count())
        .where(MeetingParticipant.meeting_id == Meeting.id)
        .correlate(Meeting)
        .scalar_subquery()
    )
    rows = await session.execute(
        select(Meeting.title, Meeting.starts_at, seconds, participants)
        .where(in_week)
        # Ties go to the earlier meeting, then by title, so the report is stable.
        .order_by((Meeting.ends_at - Meeting.starts_at).desc(), Meeting.starts_at, Meeting.title)
        .limit(TOP_N)
    )
    longest = [
        LongMeeting(title=title, starts_at=starts_at, seconds=Decimal(secs), participants=n)
        for title, starts_at, secs, n in rows
    ]
    return WeekStats(week, start, end, count, Decimal(total), longest)


def _iso(moment: datetime, tz: ZoneInfo) -> str:
    return moment.astimezone(tz).isoformat(timespec="minutes")


def _cell(text: str) -> str:
    """Keep a meeting title from running as a formula when the CSV is opened."""
    return "'" + text if text.startswith(_FORMULA_PREFIXES) else text


def render_csv(current: WeekStats, previous: WeekStats, tz: ZoneInfo) -> bytes:
    """Three labelled sections - report, Summary, Top 5 longest meetings - as UTF-8 CSV."""
    buffer = io.StringIO()
    out = csv.writer(buffer)

    out.writerow(["Weekly meetings report"])
    out.writerow(["week", str(current.week)])
    out.writerow(["previous_week", str(previous.week)])
    out.writerow(["timezone", str(tz)])
    out.writerow(["period_start", _iso(current.start, tz)])
    out.writerow(["period_end", _iso(current.end, tz)])
    out.writerow([])

    out.writerow(["Summary"])
    out.writerow(["metric", "current_week", "previous_week", "difference"])
    out.writerow(
        [
            "meetings",
            current.meetings,
            previous.meetings,
            f"{current.meetings - previous.meetings:+d}",
        ]
    )
    # The difference is taken from the rounded figures shown, so the row adds up.
    out.writerow(
        [
            "total_hours",
            current.hours,
            previous.hours,
            f"{current.hours - previous.hours:+}",
        ]
    )
    out.writerow([])

    out.writerow([f"Top {TOP_N} longest meetings"])
    out.writerow(["rank", "title", "start", "duration_hours", "participants"])
    for rank, meeting in enumerate(current.longest, start=1):
        out.writerow(
            [
                rank,
                _cell(meeting.title),
                _iso(meeting.starts_at, tz),
                _hours(meeting.seconds),
                meeting.participants,
            ]
        )
    return buffer.getvalue().encode("utf-8")


async def generate_weekly_report(
    session: AsyncSession, week: str, timezone: str | None = None
) -> bytes:
    """The report for ``week`` (e.g. "2026-W40") and the week before it."""
    tz = ZoneInfo(timezone or get_settings().report_timezone)
    current = IsoWeek.parse(week)
    this_week = await collect_week(session, current, tz)
    last_week = await collect_week(session, current.previous(), tz)
    return render_csv(this_week, last_week, tz)


async def _generate_with_own_engine(week: str) -> bytes:
    # Its own short-lived engine, so a caller's event loop never holds it.
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            return await generate_weekly_report(session, week)
    finally:
        await engine.dispose()


def build_weekly_report(week: str) -> bytes:
    """Synchronous entry point for the builder Lambda: CSV bytes for an ISO week."""
    IsoWeek.parse(week)  # reject a bad week before opening a connection
    return asyncio.run(_generate_with_own_engine(week))
