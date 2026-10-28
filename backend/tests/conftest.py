import json
import os
import time
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool


def _test_database_url() -> str:
    """Never reuse the runtime database: the schema here is dropped and recreated.

    TEST_DATABASE_URL wins if set; otherwise the runtime URL is reused with a
    "_test" suffix on the database name.
    """
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        return explicit

    runtime = os.environ.get("DATABASE_URL", "postgresql+asyncpg://peach:peach@db:5432/peach")
    base, _, database = runtime.rpartition("/")
    return f"{base}/{database}_test"


os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = _test_database_url()

from app.db import Base, get_session  # noqa: E402
from app.main import create_app  # noqa: E402


async def _ensure_test_database(url: str) -> None:
    """Create the test database if it is not there yet."""
    from sqlalchemy import text

    database = url.rsplit("/", 1)[-1]
    admin_url = url.rsplit("/", 1)[0] + "/postgres"
    admin = create_async_engine(admin_url, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            exists = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": database}
            )
            if not exists:
                await conn.execute(text(f'CREATE DATABASE "{database}"'))
    finally:
        await admin.dispose()


@pytest.fixture(scope="session")
async def engine() -> AsyncIterator:
    url = os.environ["DATABASE_URL"]
    await _ensure_test_database(url)
    engine = create_async_engine(url, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(engine) -> AsyncIterator[AsyncSession]:
    """One connection per test, wrapped in a transaction that is always rolled back."""
    connection = await engine.connect()
    transaction = await connection.begin()
    factory = async_sessionmaker(bind=connection, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session
    await transaction.rollback()
    await connection.close()


@pytest.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    app = create_app()

    async def _override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


# --- signed-in requests -------------------------------------------------------

COGNITO_ISSUER = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_TEST"
COGNITO_CLIENT_ID = "webclient123"
SIGNING_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def public_jwk(key: rsa.RSAPrivateKey, kid: str) -> dict[str, Any]:
    data = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    return {**data, "kid": kid, "alg": "RS256", "use": "sig"}


def make_token(
    key: rsa.RSAPrivateKey = SIGNING_KEY, kid: str = "k1", alg: str = "RS256", **claims: Any
) -> str:
    """A Cognito-shaped access token; override any claim, or set one to None to drop it."""
    now = int(time.time())
    payload = {
        "iss": COGNITO_ISSUER,
        "client_id": COGNITO_CLIENT_ID,
        "token_use": "access",
        "sub": "user-1",
        "username": "olena",
        "iat": now,
        "exp": now + 600,
        **claims,
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    return jwt.encode(payload, key, algorithm=alg, headers={"kid": kid})


def _reset_auth_caches() -> None:
    from app.api.auth import _keys
    from app.config import get_settings

    get_settings.cache_clear()
    _keys.cache_clear()


@pytest.fixture
def cognito(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., str]]:
    """Configure the API like production - a pinned JWKS, no network - and mint tokens."""
    monkeypatch.setenv("COGNITO_ISSUER", COGNITO_ISSUER)
    monkeypatch.setenv("COGNITO_CLIENT_ID", COGNITO_CLIENT_ID)
    monkeypatch.setenv("COGNITO_JWKS", json.dumps({"keys": [public_jwk(SIGNING_KEY, "k1")]}))
    _reset_auth_caches()
    yield make_token
    _reset_auth_caches()


@pytest.fixture
async def api(client: AsyncClient, cognito: Callable[..., str]) -> AsyncClient:
    """The test client, signed in as user-1."""
    client.headers["Authorization"] = f"Bearer {cognito()}"
    return client
