import httpx
from openai import AuthenticationError, RateLimitError

from opsmesh.modules.runtime.internal.backends.openai_agents.errors import translate_error


def test_provider_errors_preserve_retry_hints_without_secret_text() -> None:
    response = httpx.Response(
        429,
        headers={"retry-after": "12"},
        request=httpx.Request("POST", "https://provider.example/v1/responses"),
    )
    error = RateLimitError(
        "private prompt and secret", response=response, body={"code": "rate_limit_exceeded"}
    )
    mapped = translate_error(error)
    assert mapped.code == "PROVIDER_RATE_LIMITED" and mapped.retryable
    assert mapped.info.retry_after_seconds == 12
    assert mapped.info.provider_code == "rate_limit_exceeded"
    assert "private" not in mapped.info.model_dump_json()
    auth = AuthenticationError(
        "secret", response=httpx.Response(401, request=response.request), body=None
    )
    assert translate_error(auth).code == "PROVIDER_AUTHENTICATION_FAILED"
    assert not translate_error(auth).retryable
