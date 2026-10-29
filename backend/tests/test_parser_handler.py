"""The parser Lambda: Cognito auth, input limits, HTTP errors, logging - parser mocked."""

import base64
import json
import logging
import subprocess
import sys
import time
from datetime import date
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from google.genai import errors

from app import auth
from app.ai import lambda_handler as parser
from app.ai.agenda import (
    AgendaInvalid,
    AgendaParseResult,
    AgendaRefused,
    ParsedAgenda,
    Usage,
)
from app.ai.api_key import ApiKeyNotConfigured, ApiKeyUnavailable

ISSUER = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_TEST"
CLIENT_ID = "webclient123"
SECRET_TEXT = "Board meeting about the acquisition of Contoso at 10:00"
FAKE_KEY = "AIza-test-value-that-must-never-leak"

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def jwk(key: rsa.RSAPrivateKey, kid: str) -> dict[str, Any]:
    data = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    return {**data, "kid": kid, "alg": "RS256", "use": "sig"}


def token(key: rsa.RSAPrivateKey = KEY, kid: str = "k1", **claims: Any) -> str:
    now = int(time.time())
    payload = {
        "iss": ISSUER,
        "client_id": CLIENT_ID,
        "token_use": "access",
        "sub": "user-1",
        "iat": now,
        "exp": now + 600,
        **claims,
    }
    return jwt.encode(payload, key, algorithm="RS256", headers={"kid": kid})


def event(
    body: Any = None, *, bearer: str | None = "default", method: str = "POST", b64: bool = False
) -> dict[str, Any]:
    raw = json.dumps(body) if not isinstance(body, str) else body
    headers = {"content-type": "application/json"}
    if bearer == "default":
        bearer = token()
    if bearer is not None:
        headers["authorization"] = f"Bearer {bearer}"
    return {
        "version": "2.0",
        "rawPath": "/",
        "headers": headers,
        "requestContext": {"http": {"method": method, "path": "/"}},
        "body": base64.b64encode(raw.encode()).decode() if b64 else raw,
        "isBase64Encoded": b64,
    }


def result(**overrides: Any) -> AgendaParseResult:
    return AgendaParseResult(
        agenda=ParsedAgenda(title="Board meeting", participants=[]),
        model="gemini-3.5-flash-lite",
        requested_model="gemini-3.5-flash-lite",
        prompt_version="agenda-v1",
        attempts=1,
        usage=Usage(input_tokens=1200, output_tokens=300),
        **overrides,
    )


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Configure auth with a local JWKS and stand in for the model."""
    monkeypatch.setenv("COGNITO_ISSUER", ISSUER)
    monkeypatch.setenv("COGNITO_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("PARSER_MODEL", "gemini-3.5-flash-lite")
    fetches: list[str] = []

    def fetch(issuer: str) -> dict[str, Any]:
        fetches.append(issuer)
        return {"keys": [jwk(KEY, "k1")]}

    monkeypatch.setattr(auth, "_cache", auth.JwksCache(fetch))
    seen: list[dict[str, Any]] = []

    def fake_parse(text: str, today: date, tz: str) -> AgendaParseResult:
        seen.append({"text": text, "today": today, "tz": tz, "fetches": len(fetches)})
        return result()

    monkeypatch.setattr(parser, "parse_agenda", fake_parse)
    return seen


def body(response: dict[str, Any]) -> dict[str, Any]:
    return json.loads(response["body"])


# --- success ---------------------------------------------------------------------


def test_signed_in_request_is_parsed(calls: list, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    response = parser.handler(
        event({"text": SECRET_TEXT, "today": "2026-10-19", "tz": "Europe/Kyiv"}), None
    )

    assert response["statusCode"] == 200
    out = body(response)
    assert out["agenda"]["title"] == "Board meeting"
    assert out["model"] == "gemini-3.5-flash-lite"
    assert out["prompt_version"] == "agenda-v1"
    assert out["usage"] == {"input_tokens": 1200, "output_tokens": 300}
    assert out["estimated_cost_usd"] == pytest.approx(1200 * 0.30e-6 + 300 * 2.50e-6)
    assert calls == [
        {"text": SECRET_TEXT, "today": date(2026, 10, 19), "tz": "Europe/Kyiv", "fetches": 1}
    ]


def test_log_line_has_the_metrics_but_never_the_text(
    calls: list, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    parser.handler(event({"text": SECRET_TEXT}), None)
    lines = [
        json.loads(r.getMessage()) for r in caplog.records if '"agenda-parser"' in r.getMessage()
    ]
    assert len(lines) == 1
    line = lines[0]
    assert line["outcome"] == "ok" and line["status"] == 200
    assert line["model"] == "gemini-3.5-flash-lite"
    assert line["prompt_version"] == "agenda-v1"
    assert (line["input_tokens"], line["output_tokens"]) == (1200, 300)
    assert isinstance(line["latency_ms"], int)
    assert line["estimated_cost_usd"] == pytest.approx(0.00111)
    assert SECRET_TEXT not in caplog.text and "Contoso" not in caplog.text


def test_defaults_today_and_time_zone(calls: list) -> None:
    parser.handler(event({"text": "agenda"}), None)
    assert calls[0]["tz"] == "Europe/Kyiv"
    assert isinstance(calls[0]["today"], date)


def test_base64_body_is_accepted(calls: list) -> None:
    assert parser.handler(event({"text": "agenda"}, b64=True), None)["statusCode"] == 200


def test_signing_keys_are_cached_between_requests(calls: list) -> None:
    parser.handler(event({"text": "a"}), None)
    parser.handler(event({"text": "b"}), None)
    assert [c["fetches"] for c in calls] == [1, 1]


# --- authentication -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "bearer",
    [
        None,  # no header
        "not-a-jwt",
        token(OTHER_KEY),  # signed by another key, same kid
        token(kid="unknown-kid"),
        token(exp=int(time.time()) - 3600),  # expired
        token(iss="https://cognito-idp.us-east-1.amazonaws.com/us-east-1_OTHER"),
        token(client_id="someone-elses-client"),
        token(token_use="id"),  # an ID token is not an access token
        token(token_use=None),
    ],
)
def test_without_a_valid_access_token_the_answer_is_401(calls: list, bearer: str | None) -> None:
    response = parser.handler(event({"text": "agenda"}, bearer=bearer), None)
    assert response["statusCode"] == 401
    assert response["headers"]["www-authenticate"] == "Bearer"
    assert calls == []  # the model was never called


def test_wrong_auth_scheme_is_401(calls: list) -> None:
    e = event({"text": "agenda"})
    e["headers"]["authorization"] = "Basic dXNlcjpwYXNz"
    assert parser.handler(e, None)["statusCode"] == 401


def test_without_a_configured_user_pool_the_answer_is_503(
    calls: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("COGNITO_ISSUER")
    response = parser.handler(event({"text": "agenda"}), None)
    assert response["statusCode"] == 503
    assert calls == []


def test_unreachable_signing_keys_are_503(calls: list, monkeypatch: pytest.MonkeyPatch) -> None:
    def down(issuer: str) -> dict:
        raise OSError("network unreachable")

    monkeypatch.setattr(auth, "_cache", auth.JwksCache(down))
    assert parser.handler(event({"text": "agenda"}), None)["statusCode"] == 503


# --- request validation ---------------------------------------------------------------------


def test_only_post_is_allowed(calls: list) -> None:
    assert parser.handler(event({"text": "x"}, method="GET"), None)["statusCode"] == 405


@pytest.mark.parametrize("raw", ["{not json", "[1, 2]", '"just a string"'])
def test_bad_json_is_400(calls: list, raw: str) -> None:
    assert parser.handler(event(raw), None)["statusCode"] == 400
    assert calls == []


@pytest.mark.parametrize("payload", [{}, {"text": ""}, {"text": "   "}, {"text": 42}])
def test_missing_or_empty_text_is_422(calls: list, payload: dict) -> None:
    assert parser.handler(event(payload), None)["statusCode"] == 422
    assert calls == []


def test_text_over_10000_characters_is_413(calls: list) -> None:
    response = parser.handler(event({"text": "x" * 10_001}), None)
    assert response["statusCode"] == 413
    assert "10,000" in body(response)["detail"]
    assert calls == []


def test_text_of_exactly_10000_characters_is_accepted(calls: list) -> None:
    assert parser.handler(event({"text": "x" * 10_000}), None)["statusCode"] == 200


@pytest.mark.parametrize("payload", [{"tz": "Mars/Olympus"}, {"today": "next monday"}])
def test_bad_date_or_zone_is_422(calls: list, payload: dict) -> None:
    assert parser.handler(event({"text": "agenda", **payload}), None)["statusCode"] == 422


def test_auth_is_checked_before_the_body(calls: list) -> None:
    """Anonymous callers learn nothing about the input rules."""
    response = parser.handler(event({"text": "x" * 20_000}, bearer=None), None)
    assert response["statusCode"] == 401


# --- model failures map to clear HTTP errors -------------------------------------------------


def _gemini_error(code: int, status: str, message: str = "upstream says no") -> Exception:
    error = {"error": {"code": code, "status": status, "message": message}}
    return (errors.ClientError if code < 500 else errors.ServerError)(code, error)


@pytest.mark.parametrize(
    ("raised", "status", "outcome"),
    [
        (AgendaInvalid("still invalid"), 502, "invalid_after_retry"),
        (AgendaRefused("declined"), 422, "refused"),
        (ApiKeyNotConfigured("no key"), 503, "api_key_not_configured"),
        (ApiKeyUnavailable("could not read SSM parameter /x"), 503, "api_key_unavailable"),
        (_gemini_error(429, "RESOURCE_EXHAUSTED"), 429, "throttled"),
        (_gemini_error(400, "INVALID_ARGUMENT", "API key not valid."), 503, "model_unavailable"),
        (_gemini_error(403, "PERMISSION_DENIED"), 503, "model_unavailable"),
        (_gemini_error(404, "NOT_FOUND", "model not found"), 503, "model_unavailable"),
        (_gemini_error(400, "INVALID_ARGUMENT", "bad schema"), 502, "upstream_error"),
        (_gemini_error(500, "INTERNAL"), 502, "upstream_error"),
        (_gemini_error(503, "UNAVAILABLE"), 502, "upstream_error"),
        (_gemini_error(504, "DEADLINE_EXCEEDED"), 504, "timeout"),
        (httpx.ReadTimeout("timed out"), 504, "timeout"),
        (httpx.ConnectError("unreachable"), 502, "upstream_error"),
    ],
)
def test_model_errors(
    calls: list,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    raised: Exception,
    status: int,
    outcome: str,
) -> None:
    caplog.set_level(logging.INFO)

    def failing(*args: Any) -> None:
        raise raised

    monkeypatch.setattr(parser, "parse_agenda", failing)
    response = parser.handler(event({"text": "agenda"}), None)
    assert response["statusCode"] == status
    assert "detail" in body(response)
    line = json.loads(
        next(r.getMessage() for r in caplog.records if "agenda-parser" in r.getMessage())
    )
    assert line["outcome"] == outcome


def test_the_api_key_never_reaches_the_response_or_the_log(
    calls: list, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)

    def failing(*args: Any) -> None:  # an upstream message that echoes the key back
        raise _gemini_error(400, "INVALID_ARGUMENT", f"API key not valid: {FAKE_KEY}")

    monkeypatch.setattr(parser, "parse_agenda", failing)
    response = parser.handler(event({"text": SECRET_TEXT}), None)
    assert response["statusCode"] == 503
    assert FAKE_KEY not in json.dumps(response)
    assert FAKE_KEY not in caplog.text
    assert SECRET_TEXT not in caplog.text


# --- it never touches the database -------------------------------------------------------------


def test_the_parser_does_not_import_the_database() -> None:
    check = (
        "import sys, app.ai.lambda_handler; "
        "bad = [m for m in ('app.db', 'sqlalchemy', 'asyncpg', 'app.models') if m in sys.modules]; "
        "assert not bad, bad; print('ok')"
    )
    run = subprocess.run([sys.executable, "-c", check], capture_output=True, text=True)
    assert run.stdout.strip() == "ok", run.stderr
