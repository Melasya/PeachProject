"""The meetings API the paste-an-agenda form saves through (signed in; see test_meetings_auth)."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.reports.weekly import generate_weekly_report

KYIV = ZoneInfo("Europe/Kyiv")
START = datetime(2031, 3, 4, 10, 0, tzinfo=KYIV)

SUGGESTION = {
    "title": "Sprint planing",
    "starts_at": "2031-03-04T10:00:00+02:00",
    "ends_at": None,
    "organiser": "Olena",
    "participants": ["Olena", "Taras"],
    "agenda_items": [],
    "action_items": [],
}


def meeting(**overrides) -> dict:
    payload = {
        "title": "Sprint planning",
        "organiser": "Olena",
        "starts_at": START.isoformat(),
        "ends_at": (START + timedelta(minutes=90)).isoformat(),
        "participants": ["Olena", "Taras", "ivan@example.com"],
        "agenda_items": [
            {"title": "Review", "owner": "Taras", "duration_minutes": 15},
            {"title": "Pick stories"},
        ],
        "action_items": [{"description": "Send notes", "owner": "Taras", "due_date": "2031-03-07"}],
        "ai": {
            "suggestion": SUGGESTION,
            "model": "anthropic.claude-opus-5-5",
            "prompt_version": "agenda-v1",
        },
    }
    return {**payload, **overrides}


async def test_create_and_read_back(api: AsyncClient) -> None:
    created = await api.post("/api/v1/meetings", json=meeting())
    assert created.status_code == 201
    body = created.json()
    assert body["title"] == "Sprint planning"
    assert body["participants"] == sorted(["Olena", "Taras", "ivan@example.com"])
    assert [a["title"] for a in body["agenda_items"]] == ["Review", "Pick stories"]  # order kept
    assert body["agenda_items"][1] == {
        "title": "Pick stories",
        "owner": None,
        "duration_minutes": None,
    }
    assert body["action_items"][0]["due_date"] == "2031-03-07"
    # The original suggestion is kept as it was - typo and all - with what produced it.
    assert body["ai_suggestion"] == SUGGESTION
    assert (body["ai_model"], body["ai_prompt_version"]) == (
        "anthropic.claude-opus-5-5",
        "agenda-v1",
    )
    assert body["created_by"] == "user-1"  # from the token

    fetched = await api.get(f"/api/v1/meetings/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == body

    listed = (await api.get("/api/v1/meetings")).json()
    assert listed["total"] >= 1
    assert body["id"] in [m["id"] for m in listed["meetings"]]


async def test_a_meeting_without_ai_has_no_provenance(api: AsyncClient) -> None:
    body = (await api.post("/api/v1/meetings", json=meeting(ai=None))).json()
    assert (body["ai_suggestion"], body["ai_model"], body["ai_prompt_version"]) == (
        None,
        None,
        None,
    )


async def test_strings_are_trimmed(api: AsyncClient) -> None:
    body = (await api.post("/api/v1/meetings", json=meeting(title="  Retro  "))).json()
    assert body["title"] == "Retro"


@pytest.mark.parametrize(
    "overrides",
    [
        {"title": ""},
        {"title": "   "},
        {"title": "x" * 201},
        {"organiser": ""},
        {"ends_at": (START - timedelta(minutes=1)).isoformat()},
        {"ends_at": START.isoformat()},
        {"ends_at": (START + timedelta(hours=25)).isoformat()},
        {"starts_at": "2031-03-04T10:00:00"},  # no time zone
        {"participants": ["Olena", "olena"]},  # repeats
        {"participants": ["x"] * 51},
        {"agenda_items": [{"title": "t"}] * 31},
        {"agenda_items": [{"title": "t", "duration_minutes": 0}]},
        {"action_items": [{"description": "d", "due_date": "Friday"}]},
        {"unexpected": True},
        {"ai": {"suggestion": SUGGESTION, "model": "", "prompt_version": "agenda-v1"}},
        {"ai": {"suggestion": {"blob": "x" * 70_000}, "model": "m", "prompt_version": "v"}},
    ],
)
async def test_invalid_meetings_are_422(api: AsyncClient, overrides: dict) -> None:
    response = await api.post("/api/v1/meetings", json=meeting(**overrides))
    assert response.status_code == 422


async def test_missing_meeting_is_404(api: AsyncClient) -> None:
    response = await api.get("/api/v1/meetings/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


async def test_saved_meeting_counts_in_the_weekly_report(
    api: AsyncClient, session: AsyncSession
) -> None:
    await api.post("/api/v1/meetings", json=meeting())
    csv = (await generate_weekly_report(session, "2031-W10", timezone="Europe/Kyiv")).decode()
    assert "meetings,1,0,+1" in csv
    assert "Sprint planning,2031-03-04T10:00+02:00,1.50,3" in csv
