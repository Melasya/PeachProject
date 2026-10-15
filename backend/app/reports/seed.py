"""Insert demo meetings for one ISO week and the week before - explicitly.

Usage (local Compose database):

    make seed-meetings week=2026-W40
    python -m app.reports.seed --week 2026-W40 --yes        # same thing
    python -m app.reports.seed --reset --yes                # remove demo rows only

Nothing happens without --yes. It refuses to run when APP_ENV is production
unless --allow-production is also given. Demo rows are recognisable - titles
start with "[demo]" and every address is @example.com - and re-seeding a week
replaces the demo rows of those two weeks only, so real meetings are never
touched. The database URL comes from the environment and is never printed.
"""

import argparse
import asyncio
import sys
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.models import Meeting, MeetingParticipant
from app.reports.weekly import IsoWeek

DEMO_PREFIX = "[demo] "
DEMO_DOMAIN = "@example.com"

# (weekday 0=Monday, start hour, minutes, title, participants) for the report week.
CURRENT_WEEK = [
    (0, 9, 30, "Weekly planning", 6),
    (1, 10, 120, "Architecture review", 4),
    (2, 14, 45, "Customer call", 3),
    (3, 11, 90, "Sprint demo", 8),
    (3, 16, 25, "One-to-one", 2),
    (4, 13, 180, "Design workshop", 5),
    (6, 23, 50, "Late incident review", 3),
]
# Fewer and shorter meetings the week before, so the differences are visible.
PREVIOUS_WEEK = [
    (0, 9, 30, "Weekly planning", 6),
    (2, 15, 60, "Customer call", 3),
    (4, 10, 75, "Retrospective", 7),
]


def _demo_meetings(
    week: IsoWeek, plan: list[tuple[int, int, int, str, int]], tz: ZoneInfo
) -> list[tuple[Meeting, list[str]]]:
    """(meeting, participant emails) for each line of the plan, in ``tz``."""
    meetings = []
    for weekday, hour, minutes, title, people in plan:
        start = datetime.combine(week.monday + timedelta(days=weekday), time(hour), tzinfo=tz)
        meeting = Meeting(
            title=DEMO_PREFIX + title,
            organiser="organiser" + DEMO_DOMAIN,
            starts_at=start,
            ends_at=start + timedelta(minutes=minutes),
        )
        emails = [f"person{n}{DEMO_DOMAIN}" for n in range(1, people + 1)]
        meetings.append((meeting, emails))
    return meetings


def _demo_rows():
    return (Meeting.title.startswith(DEMO_PREFIX)) & (Meeting.organiser.endswith(DEMO_DOMAIN))


async def seed(session: AsyncSession, week: str, tz: ZoneInfo) -> int:
    """Replace the demo meetings of ``week`` and the week before; return how many."""
    current = IsoWeek.parse(week)
    previous = current.previous()
    start, _ = previous.bounds(tz)
    _, end = current.bounds(tz)
    await session.execute(
        delete(Meeting).where(_demo_rows(), Meeting.starts_at >= start, Meeting.starts_at < end)
    )
    planned = _demo_meetings(current, CURRENT_WEEK, tz) + _demo_meetings(
        previous, PREVIOUS_WEEK, tz
    )
    session.add_all(meeting for meeting, _ in planned)
    await session.flush()  # assigns the ids the participants point at
    session.add_all(
        MeetingParticipant(meeting_id=meeting.id, email=email)
        for meeting, emails in planned
        for email in emails
    )
    await session.flush()
    return len(planned)


async def reset(session: AsyncSession) -> int:
    """Delete every demo meeting (participants go with them); real rows are untouched."""
    result = await session.execute(delete(Meeting).where(_demo_rows()))
    return result.rowcount


async def _in_own_transaction(operation) -> int:
    """Run ``operation(session, tz)`` on a short-lived connection and commit it."""
    settings = get_settings()
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            count = await operation(session, ZoneInfo(settings.report_timezone))
            await session.commit()
            return count
    finally:
        await engine.dispose()


def seed_demo_week(week: str) -> int:
    """Synchronous entry point (CLI and the API Lambda's direct-invoke action).

    Replaces the demo meetings of ``week`` and the week before and commits;
    returns how many were written. Rejects a bad week before connecting.
    """
    week = str(IsoWeek.parse(week))
    return asyncio.run(_in_own_transaction(lambda session, tz: seed(session, week, tz)))


async def _run(args: argparse.Namespace) -> str:
    if args.reset:
        removed = await _in_own_transaction(lambda session, tz: reset(session))
        return f"removed {removed} demo meetings"
    added = await _in_own_transaction(lambda session, tz: seed(session, args.week, tz))
    return f"seeded {added} demo meetings for {args.week} and the week before"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--week", help="ISO week to seed, e.g. 2026-W40")
    target.add_argument("--reset", action="store_true", help="delete all demo meetings")
    parser.add_argument("--yes", action="store_true", help="actually write to the database")
    parser.add_argument("--allow-production", action="store_true")
    args = parser.parse_args(argv)

    if args.week:
        try:
            IsoWeek.parse(args.week)  # fail on a bad week before connecting
        except ValueError as error:
            parser.error(str(error))
    settings = get_settings()
    if settings.app_env == "production" and not args.allow_production:
        print("refusing: APP_ENV is production (add --allow-production deliberately)")
        return 2
    if not args.yes:
        print(f"dry run: would {'reset demo meetings' if args.reset else 'seed ' + args.week}")
        print("nothing written - add --yes to write to the database")
        return 1

    print(asyncio.run(_run(args)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
