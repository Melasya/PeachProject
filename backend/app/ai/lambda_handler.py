"""Agenda parser Lambda: POST pasted text, get suggested meeting fields back.

Runs outside the VPC (it needs the Gemini API, SSM for its key and Cognito's
signing keys, not RDS) behind its own function URL, with handler
``app.ai.lambda_handler.handler``.
It never touches the database: the browser saves the edited result through
the validated meetings API. CORS is answered by the function URL itself.

    POST {"text": "...", "today": "2026-10-19"?, "tz": "Europe/Kyiv"?}
    Authorization: Bearer <Cognito access token>

    200 {"agenda": {...}, "model", "prompt_version", "usage", ...}
    400 bad JSON · 401 no/invalid token · 405 not POST · 413 over 10,000 chars
    422 empty text, bad date/zone, or the model declined · 429 throttled
    502 the model's answer did not validate after a retry, or upstream error
    503 sign-in, the API key or model access not configured · 504 the model timed out

Each request logs one JSON line - model, prompt version, tokens, latency,
estimated cost, outcome - and never the pasted text or the API key.
"""

import base64
import json
import logging
import os
import time
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.ai.agenda import (
    MAX_INPUT_CHARS,
    PROMPT_VERSION,
    AgendaInvalid,
    AgendaRefused,
    AgendaTextTooLong,
    EmptyAgendaText,
    parse_agenda,
)
from app.ai.api_key import ApiKeyNotConfigured, ApiKeyUnavailable
from app.auth import AuthError, AuthNotConfigured, bearer_token, verify_access_token

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

DEFAULT_TZ = "Europe/Kyiv"


def _response(status: int, body: dict[str, Any], headers: dict[str, str] | None = None) -> dict:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json", **(headers or {})},
        "body": json.dumps(body),
    }


def _error(status: int, detail: str, **headers: str) -> dict:
    return _response(status, {"detail": detail}, headers)


def _log(**fields: Any) -> None:
    logger.info(json.dumps({"component": "agenda-parser", **fields}, default=str))


def _upstream(error: Any) -> tuple[int, str, str]:
    """A Gemini API error as (status, outcome, detail) for the browser."""
    code = getattr(error, "code", None)
    if code == 429:
        return 429, "throttled", "too many requests to the model - try again"
    if code == 504:
        return 504, "timeout", "the model took too long - try again"
    # A bad key comes back as 400 INVALID_ARGUMENT; a disabled one as 401/403.
    bad_key = code == 400 and "api key" in str(getattr(error, "message", "") or "").lower()
    if code in (401, 403, 404) or bad_key:
        return 503, "model_unavailable", "the model is not available to this service"
    return 502, "upstream_error", "the model service failed - try again"


def _body(event: dict[str, Any]) -> dict[str, Any]:
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("body must be a JSON object")
    return payload


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    started = time.monotonic()
    model = os.environ.get("PARSER_MODEL")
    base = {"prompt_version": PROMPT_VERSION, "requested_model": model}

    def done(status: int, outcome: str, response: dict, **extra: Any) -> dict:
        _log(
            event="parse",
            outcome=outcome,
            status=status,
            latency_ms=round((time.monotonic() - started) * 1000),
            **base,
            **extra,
        )
        return response

    method = (event.get("requestContext") or {}).get("http", {}).get("method", "")
    if method != "POST":
        return done(405, "method_not_allowed", _error(405, "use POST"))

    try:
        verify_access_token(bearer_token(event.get("headers")))
    except AuthNotConfigured:
        return done(503, "auth_not_configured", _error(503, "sign-in is not configured"))
    except AuthError:
        return done(
            401,
            "unauthorized",
            _error(401, "sign in to use the agenda parser", **{"www-authenticate": "Bearer"}),
        )
    except OSError:  # the signing keys could not be fetched
        return done(503, "jwks_unavailable", _error(503, "sign-in check unavailable, try again"))

    try:
        payload = _body(event)
    except ValueError, UnicodeDecodeError:
        return done(400, "bad_json", _error(400, "send a JSON object with a text field"))

    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        return done(422, "empty_text", _error(422, "paste some agenda text first"))
    if len(text) > MAX_INPUT_CHARS:
        return done(
            413,
            "too_long",
            _error(413, f"the text is longer than {MAX_INPUT_CHARS:,} characters"),
            chars=len(text),
        )

    tz_name = payload.get("tz") or os.environ.get("PARSER_TIMEZONE", DEFAULT_TZ)
    try:
        tz = ZoneInfo(str(tz_name))
        today = (
            date.fromisoformat(payload["today"])
            if payload.get("today")
            else datetime.now(tz).date()
        )
    except ZoneInfoNotFoundError, ValueError, TypeError:
        return done(422, "bad_context", _error(422, "today must be YYYY-MM-DD and tz a zone name"))

    import httpx
    from google.genai import errors

    # Upstream messages are never logged or returned: only fixed text and the status.
    try:
        result = parse_agenda(text, today, str(tz_name))
    except (EmptyAgendaText, AgendaTextTooLong) as error:  # re-checked inside; kept for safety
        return done(422, "bad_text", _error(422, str(error)))
    except AgendaRefused:
        return done(422, "refused", _error(422, "the model declined to read this text"))
    except AgendaInvalid:
        return done(
            502, "invalid_after_retry", _error(502, "could not get a valid suggestion - try again")
        )
    except ApiKeyNotConfigured:
        return done(503, "api_key_not_configured", _error(503, "AI parsing is not configured"))
    except ApiKeyUnavailable:
        return done(503, "api_key_unavailable", _error(503, "AI parsing is unavailable, try again"))
    except errors.APIError as error:
        status, outcome, detail = _upstream(error)
        return done(status, outcome, _error(status, detail), upstream_status=error.code)
    except httpx.TimeoutException:
        return done(504, "timeout", _error(504, "the model took too long - try again"))
    except httpx.TransportError:
        return done(502, "upstream_error", _error(502, "the model service failed - try again"))

    return done(
        200,
        "ok",
        _response(200, result.to_dict()),
        model=result.model,
        attempts=result.attempts,
        input_tokens=result.usage.input_tokens,
        output_tokens=result.usage.output_tokens,
        estimated_cost_usd=result.estimated_cost_usd,
        chars=len(text),
        ungrounded=len(result.ungrounded),
    )
