"""AWS Lambda entry point: the FastAPI app behind a Lambda function URL.

Three kinds of event arrive here:

- HTTP requests from the function URL (payload format 2.0), handed to the ASGI
  app through Mangum.
- ``{"action": "migrate"}``, sent by scripts/deploy-backend.sh through a direct
  invoke after every deploy.
- ``{"action": "seed_demo_meetings", "week": "2026-W41", "confirm": true}``,
  sent by hand (scripts/reports.sh seed) to fill production with demo meetings
  for the weekly report. It replaces only demo rows - titles "[demo] ...",
  addresses @example.com - of that week and the week before.

A function URL request can never produce the action shapes - its body is
nested under ``body``, not at the top level - and an event carrying any of the
HTTP payload's keys is always treated as a request, never as an action. So the
actions are reachable only by someone allowed to call lambda:InvokeFunction.
"""

import asyncio
import logging
from pathlib import Path
from typing import Any

from mangum import Mangum

from app.main import app

logger = logging.getLogger(__name__)

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"

# Mangum calls asyncio.get_event_loop(), which on Python 3.14 raises when no
# loop is set. Own one loop for the life of the execution environment, and set
# it again before every call - alembic's asyncio.run() unsets it on exit.
_loop = asyncio.new_event_loop()
_asgi = Mangum(app, lifespan="off")


def _migrate() -> dict[str, str]:
    from alembic import command
    from alembic.config import Config

    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_INI.parent / "migrations"))
    command.upgrade(config, "head")
    logger.info("migrations applied")
    return {"status": "ok", "migrated_to": "head"}


SEED_ACTION = "seed_demo_meetings"

# Keys of the function URL's HTTP payload. An event with any of them is a
# request and goes to the app, whatever else it carries.
_HTTP_KEYS = frozenset(
    {"version", "routeKey", "rawPath", "rawQueryString", "headers", "requestContext", "body"}
)


def _seed_demo_meetings(event: dict[str, Any]) -> dict[str, Any]:
    from app.reports.seed import seed_demo_week
    from app.reports.weekly import IsoWeek

    # Exactly the JSON value true: not "true", not 1.
    if event.get("confirm") is not True:
        raise ValueError(f'{SEED_ACTION} needs "confirm": true')
    week = event.get("week")
    if not isinstance(week, str):
        raise ValueError(f'{SEED_ACTION} needs "week", e.g. "2026-W41"')
    week = str(IsoWeek.parse(week))

    seeded = seed_demo_week(week)
    previous = str(IsoWeek.parse(week).previous())
    logger.info("seeded %d demo meetings for %s and %s", seeded, week, previous)
    return {"status": "ok", "action": SEED_ACTION, "weeks": [previous, week], "seeded": seeded}


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    if not _HTTP_KEYS.intersection(event):
        action = event.get("action")
        if action == "migrate":
            return _migrate()
        if action == SEED_ACTION:
            return _seed_demo_meetings(event)
    asyncio.set_event_loop(_loop)
    return _asgi(event, context)
