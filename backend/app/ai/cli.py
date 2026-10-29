"""Local commands for the agenda parser (make parse / make llm-hello).

    python -m app.ai.cli hello                         # one tiny Gemini call
    python -m app.ai.cli parse --file agenda.txt       # parse a file, print the JSON
    python -m app.ai.cli parse --file agenda.txt --dry-run   # show the request, no call

Both real commands call the Gemini API with GEMINI_API_KEY from the environment:
free on the free tier, a fraction of a cent each on the paid tier. --dry-run
makes no call and needs no key. The key is never printed.
"""

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.ai.agenda import (
    DEFAULT_EFFORT,
    DEFAULT_MODEL,
    MAX_TOKENS,
    RECORD_AGENDA_TOOL,
    SYSTEM_PROMPT,
    AgendaParseError,
    build_user_message,
    default_client,
    estimate_cost_usd,
    parse_agenda,
)
from app.ai.api_key import ApiKeyNotConfigured, ApiKeyUnavailable


def hello(model: str) -> int:
    from google.genai import types

    client = default_client()
    response = client.models.generate_content(
        model=model,
        contents="Reply with exactly: hello from Gemini",
        config=types.GenerateContentConfig(
            max_output_tokens=1024, thinking_config=types.ThinkingConfig(thinking_level="LOW")
        ),
    )
    candidate = (response.candidates or [None])[0]
    reason = getattr(getattr(candidate, "finish_reason", None), "value", None)
    print(
        json.dumps(
            {"model": response.model_version, "finish_reason": reason, "text": response.text}
        )
    )
    return 0


def parse(path: Path, today: date, tz: str, model: str, dry_run: bool) -> int:
    text = path.read_text(encoding="utf-8")
    if dry_run:
        print(
            json.dumps(
                {
                    "model": model,
                    "max_output_tokens": MAX_TOKENS,
                    "thinking_level": DEFAULT_EFFORT.upper(),
                    "function_calling": {"mode": "ANY", "allowed": [RECORD_AGENDA_TOOL["name"]]},
                    "system_chars": len(SYSTEM_PROMPT),
                    "user_message": build_user_message(text, today, tz),
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0
    try:
        result = parse_agenda(text, today, tz, model=model)
    except AgendaParseError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    out = result.to_dict()
    out["estimated_cost_usd"] = estimate_cost_usd(result.model, result.usage)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    h = sub.add_parser("hello", help="one tiny Gemini call to check the key and model")
    model = os.environ.get("PARSER_MODEL") or DEFAULT_MODEL
    h.add_argument("--model", default=model)
    p = sub.add_parser("parse", help="parse an agenda file")
    p.add_argument("--file", type=Path, required=True)
    p.add_argument("--tz", default="Europe/Kyiv")
    p.add_argument("--today", type=date.fromisoformat, default=None)
    p.add_argument("--model", default=model)
    p.add_argument("--dry-run", action="store_true", help="print the request, call nothing")
    args = parser.parse_args(argv)

    try:
        if args.command == "hello":
            return hello(args.model)
        today = args.today or datetime.now(ZoneInfo(args.tz)).date()
        return parse(args.file, today, args.tz, args.model, args.dry_run)
    except (ApiKeyNotConfigured, ApiKeyUnavailable) as error:
        print(f"error: {error} (see README section 16)", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
