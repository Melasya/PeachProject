"""Verify Cognito access tokens - for the API and the agenda parser alike.

Tokens are RS256 JWTs signed with the user pool's keys (its JWKS). Where those
keys come from depends on where the code runs:

- The agenda parser runs outside the VPC and fetches the JWKS itself. Keys are
  cached for an hour and refetched once when a token names a key the cache does
  not have (rotation).
- The API runs in the VPC with no route to the internet, so it cannot fetch
  anything. scripts/deploy-backend.sh fetches the JWKS at deploy time and pins
  it in COGNITO_JWKS; verification is then entirely local. The keys are public,
  so pinning them is not a secret, and a key the pin does not know is a 401 -
  redeploy the backend if Cognito ever rotates its keys.

Nothing here imports the database layer: the parser must stay free of it.
"""

import json
import os
import time
import urllib.request
from collections.abc import Callable
from typing import Any

import jwt

JWKS_TTL_SECONDS = 3600


class AuthError(Exception):
    """Missing, malformed, expired or foreign token - answer 401."""


class AuthNotConfigured(Exception):
    """No user pool configured - answer 503 rather than run unauthenticated."""


def _fetch_jwks(issuer: str) -> dict[str, Any]:
    with urllib.request.urlopen(f"{issuer}/.well-known/jwks.json", timeout=5) as response:
        return json.load(response)


class JwksCache:
    def __init__(self, fetch: Callable[[str], dict[str, Any]] = _fetch_jwks) -> None:
        self._fetch = fetch
        self._keys: dict[str, Any] = {}
        self._fetched_at = 0.0

    def key(self, issuer: str, kid: str) -> Any:
        stale = time.monotonic() - self._fetched_at > JWKS_TTL_SECONDS
        if stale or kid not in self._keys:
            self._keys = {k["kid"]: k for k in self._fetch(issuer).get("keys", [])}
            self._fetched_at = time.monotonic()
        if kid not in self._keys:
            raise AuthError("token signed with an unknown key")
        return jwt.PyJWK(self._keys[kid]).key


def pinned_jwks(raw: str) -> JwksCache:
    """A cache over a JWKS given as JSON text, that never touches the network."""
    try:
        jwks = json.loads(raw)
        keys = jwks["keys"]
        if not keys or not all(
            k.get("kty") == "RSA" and k.get("kid") and k.get("n") and k.get("e") for k in keys
        ):
            raise ValueError("not a list of RSA public keys")
        for k in keys:
            jwt.PyJWK(k)  # rejects anything PyJWT cannot use as a key
    except (ValueError, KeyError, TypeError, AttributeError, jwt.PyJWTError) as error:
        raise AuthNotConfigured(f"COGNITO_JWKS is not a usable JWKS: {error}") from None
    return JwksCache(lambda _issuer: jwks)


_cache = JwksCache()


def bearer_token(headers: Any) -> str:
    """The token from an Authorization: Bearer header (any mapping of headers)."""
    # Function URL events lower-case header names; be tolerant anyway.
    value = next((v for k, v in dict(headers or {}).items() if k.lower() == "authorization"), "")
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthError("missing bearer token")
    return token.strip()


def verify_access_token(
    token: str,
    *,
    issuer: str | None = None,
    client_id: str | None = None,
    cache: JwksCache | None = None,
) -> dict[str, Any]:
    """The token's claims, or AuthError. Only access tokens for our client pass."""
    issuer = (issuer or os.environ.get("COGNITO_ISSUER", "")).rstrip("/")
    client_id = client_id or os.environ.get("COGNITO_CLIENT_ID", "")
    if not issuer or not client_id:
        raise AuthNotConfigured("COGNITO_ISSUER / COGNITO_CLIENT_ID are not set")
    try:
        header = jwt.get_unverified_header(token)
        signing_key = (cache or _cache).key(issuer, header.get("kid", ""))
        claims = jwt.decode(
            token,
            key=signing_key,
            algorithms=["RS256"],
            issuer=issuer,
            # Access tokens carry client_id, not aud.
            options={
                "require": ["exp", "iss", "sub", "token_use", "client_id"],
                "verify_aud": False,
            },
            leeway=30,
        )
    except AuthError:
        raise
    except jwt.PyJWTError as error:
        raise AuthError(f"invalid token: {error}") from None
    if claims.get("token_use") != "access":
        raise AuthError("not an access token")
    if claims.get("client_id") != client_id:
        raise AuthError("token issued for another client")
    return claims
