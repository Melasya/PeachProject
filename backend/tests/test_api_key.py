"""Where the Gemini key comes from - the environment or SSM, faked; no AWS, no network."""

from typing import Any

import pytest
from botocore.exceptions import ClientError

from app.ai import api_key
from app.ai.api_key import ApiKeyNotConfigured, ApiKeyUnavailable, gemini_api_key

FAKE_KEY = "AIza-test-value-that-must-never-leak"
PARAMETER = "/peach/gemini-api-key"


class FakeSsm:
    def __init__(self, value: str | None = FAKE_KEY, error: Exception | None = None) -> None:
        self.value, self.error = value, error
        self.calls: list[dict[str, Any]] = []

    def get_parameter(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return {"Parameter": {"Name": kwargs["Name"], "Type": "SecureString", "Value": self.value}}


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY_PARAMETER", raising=False)
    api_key._from_ssm.cache_clear()
    yield
    api_key._from_ssm.cache_clear()


def use_ssm(monkeypatch: pytest.MonkeyPatch, ssm: FakeSsm) -> FakeSsm:
    import boto3

    monkeypatch.setenv("GEMINI_API_KEY_PARAMETER", PARAMETER)
    monkeypatch.setattr(boto3, "client", lambda service, **_: ssm if service == "ssm" else None)
    return ssm


def test_the_environment_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    ssm = use_ssm(monkeypatch, FakeSsm(value="from-ssm"))
    monkeypatch.setenv("GEMINI_API_KEY", f"  {FAKE_KEY}\n")
    assert gemini_api_key() == FAKE_KEY
    assert ssm.calls == []


def test_on_lambda_the_key_is_read_decrypted_from_ssm_once(monkeypatch: pytest.MonkeyPatch) -> None:
    ssm = use_ssm(monkeypatch, FakeSsm())
    assert gemini_api_key() == FAKE_KEY
    assert gemini_api_key() == FAKE_KEY
    assert ssm.calls == [{"Name": PARAMETER, "WithDecryption": True}]


def test_neither_set_is_not_configured() -> None:
    with pytest.raises(ApiKeyNotConfigured):
        gemini_api_key()


@pytest.mark.parametrize(
    "ssm",
    [
        FakeSsm(error=ClientError({"Error": {"Code": "ParameterNotFound"}}, "GetParameter")),
        FakeSsm(error=ClientError({"Error": {"Code": "AccessDeniedException"}}, "GetParameter")),
        FakeSsm(value="   "),
    ],
)
def test_an_unreadable_parameter_is_unavailable_and_retried_later(
    monkeypatch: pytest.MonkeyPatch, ssm: FakeSsm
) -> None:
    use_ssm(monkeypatch, ssm)
    with pytest.raises(ApiKeyUnavailable, match=PARAMETER):
        gemini_api_key()
    with pytest.raises(ApiKeyUnavailable):
        gemini_api_key()
    assert len(ssm.calls) == 2  # failures are not cached


def test_errors_never_carry_the_value(monkeypatch: pytest.MonkeyPatch) -> None:
    use_ssm(monkeypatch, FakeSsm(value=FAKE_KEY))
    assert gemini_api_key() == FAKE_KEY
    api_key._from_ssm.cache_clear()
    use_ssm(monkeypatch, FakeSsm(value=""))
    with pytest.raises(ApiKeyUnavailable) as raised:
        gemini_api_key()
    assert FAKE_KEY not in str(raised.value)
