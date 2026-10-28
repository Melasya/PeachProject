"""Who is calling: a FastAPI dependency over app.auth for signed-in endpoints.

The caller's identity comes only from a verified Cognito access token, never
from the request body. There is no organisation model: every signed-in user of
the pool is treated alike (see docs/adr/0006-llm-agenda-parsing.md).
"""

from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.auth import (
    AuthError,
    AuthNotConfigured,
    JwksCache,
    bearer_token,
    pinned_jwks,
    verify_access_token,
)
from app.config import get_settings


@dataclass(frozen=True)
class CurrentUser:
    sub: str  # Cognito's stable user id
    username: str | None


@lru_cache
def _keys(pinned: str, may_fetch: bool) -> JwksCache:
    if pinned:
        return pinned_jwks(pinned)
    if may_fetch:
        return JwksCache()  # local development: the container has internet access
    raise AuthNotConfigured("COGNITO_JWKS is not set")


def current_user(request: Request) -> CurrentUser:
    # A plain def: FastAPI runs it in the threadpool, so a JWKS fetch in
    # development never blocks the event loop.
    settings = get_settings()
    try:
        if not settings.cognito_issuer or not settings.cognito_client_id:
            raise AuthNotConfigured("COGNITO_ISSUER / COGNITO_CLIENT_ID are not set")
        claims = verify_access_token(
            bearer_token(request.headers),
            issuer=settings.cognito_issuer,
            client_id=settings.cognito_client_id,
            cache=_keys(settings.cognito_jwks, settings.app_env != "production"),
        )
    except AuthNotConfigured:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="Sign-in is not configured"
        ) from None
    except AuthError:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Sign in to continue",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    except OSError:  # development only: the keys could not be fetched
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="Sign-in check unavailable"
        ) from None
    return CurrentUser(sub=claims["sub"], username=claims.get("username"))


CurrentUserDep = Annotated[CurrentUser, Depends(current_user)]
