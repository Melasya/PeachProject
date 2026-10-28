"""Local commands for the agenda parser (make parse / make llm-hello).

    python -m app.ai.cli hello                         # one tiny Bedrock call
    python -m app.ai.cli parse --file agenda.txt       # parse a file, print the JSON
    python -m app.ai.cli parse --file agenda.txt --dry-run   # show the request, no call

Both real commands call Amazon Bedrock with your local AWS credentials and
cost money (a fraction of a cent each). --dry-run makes no call at all.
"""

import argparse
import json
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


def hello(model: str) -> int:
    client = default_client()
    response = client.beta.messages.create(
        model=model,
        max_tokens=1024,
        output_config={"effort": "low"},
        messages=[{"role": "user", "content": "Reply with exactly: hello from Bedrock"}],
    )
    text = next((b.text for b in response.content if b.type == "text"), "")
    print(json.dumps({"model": response.model, "stop_reason": response.stop_reason, "text": text}))
    return 0


def parse(path: Path, today: date, tz: str, model: str, dry_run: bool) -> int:
    text = path.read_text(encoding="utf-8")
    if dry_run:
        print(
            json.dumps(
                {
                    "model": model,
                    "max_tokens": MAX_TOKENS,
                    "output_config": {"effort": DEFAULT_EFFORT},
                    "tool_choice": {"type": "auto"},
                    "tools": [RECORD_AGENDA_TOOL["name"]],
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
    h = sub.add_parser("hello", help="one tiny Bedrock call to check access")
    h.add_argument("--model", default=DEFAULT_MODEL)
    p = sub.add_parser("parse", help="parse an agenda file")
    p.add_argument("--file", type=Path, required=True)
    p.add_argument("--tz", default="Europe/Kyiv")
    p.add_argument("--today", type=date.fromisoformat, default=None)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--dry-run", action="store_true", help="print the request, call nothing")
    args = parser.parse_args(argv)

    if args.command == "hello":
        return hello(args.model)
    today = args.today or datetime.now(ZoneInfo(args.tz)).date()
    return parse(args.file, today, args.tz, args.model, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
