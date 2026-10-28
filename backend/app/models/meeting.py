import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Meeting(Base):
    """A meeting with an organiser and a start and end. Feeds the weekly report."""

    __tablename__ = "meetings"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="ck_meetings_ends_after_starts"),
        Index("ix_meetings_starts_at", "starts_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    # The organiser's name or email address.
    organiser: Mapped[str] = mapped_column(String(254), nullable=False)
    # Stored as timestamptz: an instant, whatever zone it was entered in.
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # When the form was pre-filled by the agenda parser: its original
    # suggestion, untouched by the person's edits, and what produced it.
    ai_suggestion: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    ai_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ai_prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Who saved it through the API: the Cognito user id (sub) from the verified
    # access token, never from the request body. Empty for the Lab 5 demo rows.
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class MeetingParticipant(Base):
    """One person invited to a meeting; the report counts these rows."""

    __tablename__ = "meeting_participants"

    meeting_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("meetings.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # Part of the key, so the same person cannot be counted twice. Holds a name
    # or an email address - a pasted agenda often names people without emails.
    email: Mapped[str] = mapped_column(String(254), primary_key=True)


class MeetingAgendaItem(Base):
    __tablename__ = "meeting_agenda_items"
    __table_args__ = (
        CheckConstraint(
            "duration_minutes IS NULL OR duration_minutes > 0",
            name="ck_meeting_agenda_items_duration_positive",
        ),
        UniqueConstraint("meeting_id", "position", name="uq_meeting_agenda_items_position"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    meeting_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)


class MeetingActionItem(Base):
    __tablename__ = "meeting_action_items"
    __table_args__ = (
        UniqueConstraint("meeting_id", "position", name="uq_meeting_action_items_position"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    meeting_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
