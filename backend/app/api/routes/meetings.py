import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.auth import CurrentUserDep, current_user
from app.db import SessionDep
from app.schemas import MeetingCreate, MeetingList, MeetingRead
from app.services import meetings as meetings_service

# Every endpoint needs a signed-in user: meetings hold names and email
# addresses, and they feed the emailed weekly report.
router = APIRouter(prefix="/meetings", tags=["meetings"], dependencies=[Depends(current_user)])


@router.get("", response_model=MeetingList, summary="List meetings, latest first")
async def list_meetings(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> MeetingList:
    meetings, total = await meetings_service.list_meetings(session, limit=limit, offset=offset)
    return MeetingList(meetings=meetings, total=total)


@router.post(
    "",
    response_model=MeetingRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a meeting (optionally with the AI suggestion it started from)",
)
async def create_meeting(
    payload: MeetingCreate, session: SessionDep, user: CurrentUserDep
) -> MeetingRead:
    meeting_id = await meetings_service.create_meeting(session, payload, created_by=user.sub)
    meeting = await meetings_service.get_meeting(session, meeting_id)
    assert meeting is not None
    return meeting


@router.get("/{meeting_id}", response_model=MeetingRead)
async def get_meeting(meeting_id: uuid.UUID, session: SessionDep) -> MeetingRead:
    meeting = await meetings_service.get_meeting(session, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    return meeting
