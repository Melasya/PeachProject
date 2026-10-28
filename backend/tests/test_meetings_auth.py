"""Every meetings endpoint needs a valid Cognito access token; the rest of the API does not."""

import json
import time
import urllib.request
from collections.abc import Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import jwt
import pytest
from conftest import COGNITO_CLIENT_ID, COGNITO_ISSUER, SIGNING_KEY, make_token, public_jwk
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Meeting

START = datetime(2031, 3, 4, 10, 0, tzinfo=ZoneInfo("Europe/Kyiv"))
MEETING = {
    "title": "Sprint planning",
    "organiser": "Olena",
    "starts_at": START.isoformat(),
    "ends_at": (START + timedelta(hours=1)).isoformat(),
    "participants": ["Olena"],
}
MISSING_ID = "00000000-0000-0000-0000-000000000000"
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)

ENDPOINTS = [
    ("GET", "/api/v1/meetings", None),
    ("GET", f"/api/v1/meetings/{MISSING_ID}", None),
    ("POST", "/api/v1/meetings", MEETING),
]


def bearer(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"}


async def count(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(Meeting)) or 0


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """The API Lambda has no internet: verification must never try to fetch keys."""

    def refuse(*args, **kwargs):
        raise AssertionError("tried to fetch the JWKS over the network")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)


# --- no token -------------------------------------------------------------------


@pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS)
async def test_no_token_is_401(
    client: AsyncClient, session: AsyncSession, cognito, method, path, body
) -> None:
    before = await count(session)
    response = await client.request(method, path, json=body)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert await count(session) == before  # nothing written


# --- invalid tokens -------------------------------------------------------------

now = int(time.time())

INVALID: dict[str, Callable[[], dict[str, str]]] = {
    "garbage": lambda: bearer("not-a-jwt"),
    "empty bearer": lambda: {"Authorization": "Bearer "},
    "basic scheme": lambda: {"Authorization": f"Basic {make_token()}"},
    "token without scheme": lambda: {"Authorization": make_token()},
    "signed by another key": lambda: bearer(make_token(key=OTHER_KEY)),
    "unknown kid": lambda: bearer(make_token(kid="k2")),
    "expired": lambda: bearer(make_token(iat=now - 7200, exp=now - 3600)),
    "id token": lambda: bearer(make_token(token_use="id")),
    "another client": lambda: bearer(make_token(client_id="someone-else")),
    "another user pool": lambda: bearer(
        make_token(iss="https://cognito-idp.us-east-1.amazonaws.com/us-east-1_OTHER")
    ),
    "no sub": lambda: bearer(make_token(sub=None)),
    "no expiry": lambda: bearer(make_token(exp=None)),
    "alg none": lambda: bearer(
        jwt.encode(
            {
                "iss": COGNITO_ISSUER,
                "client_id": COGNITO_CLIENT_ID,
                "token_use": "access",
                "sub": "user-1",
                "exp": now + 600,
            },
            None,
            algorithm="none",
            headers={"kid": "k1"},
        )
    ),
    "tampered payload": lambda: bearer(_tampered()),
}


def _tampered() -> str:
    header, payload, signature = make_token().split(".")
    claims = json.loads(jwt.utils.base64url_decode(payload))
    claims["sub"] = "admin"
    forged = jwt.utils.base64url_encode(json.dumps(claims).encode()).decode()
    return f"{header}.{forged}.{signature}"


@pytest.mark.parametrize("case", INVALID)
@pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS)
async def test_invalid_token_is_401(
    client: AsyncClient,
    session: AsyncSession,
    cognito,
    no_network,
    case: str,
    method: str,
    path: str,
    body: dict | None,
) -> None:
    before = await count(session)
    response = await client.request(method, path, json=body, headers=INVALID[case]())
    assert response.status_code == 401, case
    assert response.headers["www-authenticate"] == "Bearer"
    assert await count(session) == before


# --- valid tokens ----------------------------------------------------------------


async def test_valid_token_can_create_list_and_read(
    client: AsyncClient, cognito, no_network
) -> None:
    headers = bearer(cognito(sub="user-42"))

    created = await client.post("/api/v1/meetings", json=MEETING, headers=headers)
    assert created.status_code == 201
    meeting = created.json()
    assert meeting["created_by"] == "user-42"

    listed = await client.get("/api/v1/meetings", headers=headers)
    assert listed.status_code == 200
    assert meeting["id"] in [m["id"] for m in listed.json()["meetings"]]

    fetched = await client.get(f"/api/v1/meetings/{meeting['id']}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json() == meeting


async def test_token_within_clock_skew_is_accepted(client: AsyncClient, cognito) -> None:
    headers = bearer(cognito(exp=int(time.time()) - 10))  # leeway is 30 s
    assert (await client.get("/api/v1/meetings", headers=headers)).status_code == 200


async def test_the_creator_cannot_be_set_by_the_client(
    client: AsyncClient, session: AsyncSession, cognito
) -> None:
    headers = bearer(cognito(sub="user-1"))
    before = await count(session)
    for extra in ({"created_by": "admin"}, {"organisation_id": "acme"}, {"user_id": "admin"}):
        response = await client.post("/api/v1/meetings", json={**MEETING, **extra}, headers=headers)
        assert response.status_code == 422, extra
    assert await count(session) == before


# --- misconfiguration fails closed ------------------------------------------------


def _reset() -> None:
    from app.api.auth import _keys
    from app.config import get_settings

    get_settings.cache_clear()
    _keys.cache_clear()


@pytest.mark.parametrize("unset", ["COGNITO_ISSUER", "COGNITO_CLIENT_ID"])
async def test_without_a_user_pool_it_is_503_not_open(
    client: AsyncClient, cognito, monkeypatch: pytest.MonkeyPatch, unset: str
) -> None:
    monkeypatch.delenv(unset)
    _reset()
    response = await client.get("/api/v1/meetings", headers=bearer(cognito()))
    assert response.status_code == 503


async def test_production_without_pinned_keys_is_503_and_never_fetches(
    client: AsyncClient, cognito, no_network, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("COGNITO_JWKS")
    monkeypatch.setenv("APP_ENV", "production")
    _reset()
    response = await client.get("/api/v1/meetings", headers=bearer(cognito()))
    assert response.status_code == 503


@pytest.mark.parametrize(
    "jwks",
    [
        "not json",
        "{}",
        '{"keys": []}',
        '{"keys": [{"kty": "oct", "kid": "k1", "k": "c2VjcmV0"}]}',  # a shared secret, not RSA
        json.dumps({"keys": [{**public_jwk(SIGNING_KEY, "k1"), "n": "!!"}]}),
    ],
)
async def test_a_broken_pinned_jwks_is_503(
    client: AsyncClient, cognito, no_network, monkeypatch: pytest.MonkeyPatch, jwks: str
) -> None:
    monkeypatch.setenv("COGNITO_JWKS", jwks)
    _reset()
    response = await client.get("/api/v1/meetings", headers=bearer(cognito()))
    assert response.status_code == 503


# --- the rest of the API is unchanged ----------------------------------------------


async def test_health_and_items_still_need_no_token(client: AsyncClient, cognito) -> None:
    assert (await client.get("/health")).status_code == 200
    assert (await client.get("/api/v1/items")).status_code == 200
    created = await client.post("/api/v1/items", json={"name": "Still public"})
    assert created.status_code == 201
