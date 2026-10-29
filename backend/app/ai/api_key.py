"""Where the Gemini API key comes from - the backend only, never the browser.

- Locally: ``GEMINI_API_KEY`` in the environment (``.env``, which git ignores).
- On Lambda: ``GEMINI_API_KEY_PARAMETER`` names an SSM SecureString; the value is
  read and decrypted once per container and kept in memory only. It is never
  put in an environment variable, a CloudFormation parameter or a log line.

Errors name the parameter, never the value.
"""

import os
from functools import lru_cache


class ApiKeyNotConfigured(Exception):
    """No key in the environment and no parameter to read it from."""


class ApiKeyUnavailable(Exception):
    """The parameter is named but could not be read (missing, denied, network)."""


def gemini_api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        return key
    name = os.environ.get("GEMINI_API_KEY_PARAMETER", "").strip()
    if not name:
        raise ApiKeyNotConfigured("set GEMINI_API_KEY or GEMINI_API_KEY_PARAMETER")
    return _from_ssm(name)


@lru_cache(maxsize=1)  # a failure raises, so it is not cached and the next request retries
def _from_ssm(name: str) -> str:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError

    try:
        response = boto3.client("ssm").get_parameter(Name=name, WithDecryption=True)
    except (BotoCoreError, ClientError) as error:
        raise ApiKeyUnavailable(f"could not read SSM parameter {name}") from error
    value = (response.get("Parameter") or {}).get("Value", "").strip()
    if not value:
        raise ApiKeyUnavailable(f"SSM parameter {name} is empty")
    return value
