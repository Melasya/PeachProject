import uuid
from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Meeting, MeetingActionItem, MeetingAgendaItem, MeetingParticipant
from app.schemas import MeetingCreate, MeetingRead


async def create_meeting(
    session: AsyncSession, payload: MeetingCreate, *, created_by: str
) -> uuid.UUID:
    meeting = Meeting(
        title=payload.title,
        organiser=payload.organiser,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        ai_suggestion=payload.ai.suggestion if payload.ai else None,
        ai_model=payload.ai.model if payload.ai else None,
        ai_prompt_version=payload.ai.prompt_version if payload.ai else None,
        created_by=created_by,
    )
    session.add(meeting)
    await session.flush()  # assigns the id the children point at
    session.add_all(
        MeetingParticipant(meeting_id=meeting.id, email=p) for p in payload.participants
    )
    session.add_all(
        MeetingAgendaItem(meeting_id=meeting.id, position=n, **item.model_dump())
        for n, item in enumerate(payload.agenda_items)
    )
    session.add_all(
        MeetingActionItem(meeting_id=meeting.id, position=n, **item.model_dump())
        for n, item in enumerate(payload.action_items)
    )
    await session.flush()
    return meeting.id


async def _read(session: AsyncSession, meetings: list[Meeting]) -> list[MeetingRead]:
    """Children for many meetings in three queries, not three per meeting."""
    ids = [m.id for m in meetings]
    people: dict[uuid.UUID, list[str]] = defaultdict(list)
    agenda: dict[uuid.UUID, list[MeetingAgendaItem]] = defaultdict(list)
    actions: dict[uuid.UUID, list[MeetingActionItem]] = defaultdict(list)
    if ids:
        for row in await session.scalars(
            select(MeetingParticipant)
            .where(MeetingParticipant.meeting_id.in_(ids))
            .order_by(MeetingParticipant.email)
        ):
            people[row.meeting_id].append(row.email)
        for row in await session.scalars(
            select(MeetingAgendaItem)
            .where(MeetingAgendaItem.meeting_id.in_(ids))
            .order_by(MeetingAgendaItem.position)
        ):
            agenda[row.meeting_id].append(row)
        for row in await session.scalars(
            select(MeetingActionItem)
            .where(MeetingActionItem.meeting_id.in_(ids))
            .order_by(MeetingActionItem.position)
        ):
            actions[row.meeting_id].append(row)
    return [
        MeetingRead(
            id=m.id,
            title=m.title,
            organiser=m.organiser,
            starts_at=m.starts_at,
            ends_at=m.ends_at,
            created_at=m.created_at,
            participants=people[m.id],
            agenda_items=agenda[m.id],
            action_items=actions[m.id],
            ai_model=m.ai_model,
            ai_prompt_version=m.ai_prompt_version,
            ai_suggestion=m.ai_suggestion,
            created_by=m.created_by,
        )
        for m in meetings
    ]


async def get_meeting(session: AsyncSession, meeting_id: uuid.UUID) -> MeetingRead | None:
    meeting = await session.get(Meeting, meeting_id)
    if meeting is None:
        return None
    await session.refresh(meeting)  # server defaults such as created_at
    return (await _read(session, [meeting]))[0]


async def list_meetings(
    session: AsyncSession, *, limit: int, offset: int
) -> tuple[list[MeetingRead], int]:
    total = await session.scalar(select(func.count()).select_from(Meeting)) or 0
    result = await session.scalars(
        select(Meeting).order_by(Meeting.starts_at.desc()).limit(limit).offset(offset)
    )
    return await _read(session, list(result)), total
