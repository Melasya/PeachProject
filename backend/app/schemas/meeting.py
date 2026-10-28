import json
import uuid
from datetime import date, datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


def _text(max_length: int) -> StringConstraints:
    return StringConstraints(strip_whitespace=True, min_length=1, max_length=max_length)


Title = Annotated[str, _text(200)]
Person = Annotated[str, _text(254)]
Owner = Annotated[str, _text(120)]
Description = Annotated[str, _text(500)]
ModelName = Annotated[str, _text(100)]
PromptVersion = Annotated[str, _text(32)]

MAX_SUGGESTION_BYTES = 64 * 1024


class AgendaItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Title
    owner: Owner | None = None
    duration_minutes: int | None = Field(default=None, ge=1, le=600)


class ActionItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: Description
    owner: Owner | None = None
    due_date: date | None = None


class AiProvenance(BaseModel):
    """The parser's original suggestion, kept as it was before any edit."""

    model_config = ConfigDict(extra="forbid")

    suggestion: dict[str, Any]
    model: ModelName
    prompt_version: PromptVersion

    @model_validator(mode="after")
    def _bounded(self) -> AiProvenance:
        if len(json.dumps(self.suggestion)) > MAX_SUGGESTION_BYTES:
            raise ValueError(f"suggestion is larger than {MAX_SUGGESTION_BYTES} bytes")
        return self


class MeetingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Title
    organiser: Person
    starts_at: datetime
    ends_at: datetime
    participants: list[Person] = Field(default_factory=list, max_length=50)
    agenda_items: list[AgendaItemIn] = Field(default_factory=list, max_length=30)
    action_items: list[ActionItemIn] = Field(default_factory=list, max_length=30)
    ai: AiProvenance | None = None

    @model_validator(mode="after")
    def _consistent(self) -> MeetingCreate:
        for name in ("starts_at", "ends_at"):
            if getattr(self, name).utcoffset() is None:
                raise ValueError(f"{name} must include a time zone")
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        if (self.ends_at - self.starts_at).total_seconds() > 24 * 3600:
            raise ValueError("a meeting cannot be longer than 24 hours")
        folded = [p.casefold() for p in self.participants]
        if len(set(folded)) != len(folded):
            raise ValueError("participants must not repeat")
        return self


class AgendaItemRead(AgendaItemIn):
    model_config = ConfigDict(from_attributes=True)


class ActionItemRead(ActionItemIn):
    model_config = ConfigDict(from_attributes=True)


class MeetingRead(BaseModel):
    id: uuid.UUID
    title: str
    organiser: str
    starts_at: datetime
    ends_at: datetime
    created_at: datetime
    participants: list[str]
    agenda_items: list[AgendaItemRead]
    action_items: list[ActionItemRead]
    ai_model: str | None
    ai_prompt_version: str | None
    ai_suggestion: dict[str, Any] | None
    created_by: str | None


class MeetingList(BaseModel):
    meetings: list[MeetingRead]
    total: int
