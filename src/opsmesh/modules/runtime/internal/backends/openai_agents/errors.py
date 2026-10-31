"""Keep provider exception text out of public errors while preserving known usage."""

import re
from math import isfinite
from typing import Any

from opsmesh.framework.errors import OpsMeshError

from .usage import observe_usage


def translate_error(error: Exception) -> OpsMeshError:
    name = type(error).__name__
    # SDK function execution wraps application failures in UserError. Retain the
    # platform's safe classification instead of reducing permission revocations
    # and resource limits to an unclassified backend failure.
    if name == "UserError" and isinstance(error.__cause__, OpsMeshError):
        return error.__cause__
    status = getattr(error, "status_code", None)
    retryable = False
    if status == 429:
        code, message, retryable = (
            "PROVIDER_RATE_LIMITED",
            "The model provider is rate limiting requests",
            True,
        )
    elif status == 401:
        code, message = "PROVIDER_AUTHENTICATION_FAILED", "Model provider credentials were rejected"
    elif status == 403:
        code, message = (
            "PROVIDER_PERMISSION_DENIED",
            "The provider denied access to this model or resource",
        )
    elif status == 404:
        code, message = "MODEL_NOT_FOUND", "The configured provider model or resource was not found"
    elif isinstance(status, int) and status >= 500:
        code, message, retryable = "PROVIDER_UNAVAILABLE", "The model provider is unavailable", True
    elif "Connection" in name:
        code, message, retryable = (
            "PROVIDER_CONNECTION_FAILED",
            "The model provider connection failed",
            True,
        )
    elif "Guardrail" in name and "Tripwire" in name:
        code, message = "GUARDRAIL_BLOCKED", "Execution blocked by a guardrail"
    elif "Timeout" in name:
        code, message = "MODEL_TIMEOUT", "Model or tool execution timed out"
        retryable = True
    elif "MaxTurns" in name:
        code, message = "RUN_LIMIT_EXCEEDED", "Agent exceeded its turn limit"
    elif "ModelBehavior" in name:
        code, message = "MODEL_OUTPUT_INVALID", "Model output was invalid"
    elif isinstance(error, (ValueError, TypeError)):
        code, message = "CONFIG_INVALID", "SDK configuration or input was invalid"
    else:
        code, message = "BACKEND_ERROR", "Agent SDK execution failed"
    details: dict[str, Any] = {"exception_type": name}
    run_data = getattr(error, "run_data", None)
    if run_data is not None:
        details["usage"] = observe_usage(run_data).model_dump(mode="json")
    provider_code = getattr(error, "code", None)
    if not isinstance(provider_code, str) or not re.fullmatch(
        r"[A-Za-z0-9_.-]{1,64}", provider_code
    ):
        provider_code = None
    phase = {
        "GUARDRAIL_BLOCKED": "guardrail",
        "CONFIG_INVALID": "configuration",
        "RUN_LIMIT_EXCEEDED": "runtime",
        "BACKEND_ERROR": "runtime",
    }.get(code, "model")
    return OpsMeshError(
        code,
        message,
        details=details,
        retryable=retryable,
        phase=phase,
        provider="openai_agents",
        provider_code=provider_code,
        retry_after_seconds=_retry_after(error),
    )


def _retry_after(error: Exception) -> float | None:
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", {})
    try:
        seconds = float(headers.get("retry-after", "nan"))
    except (TypeError, ValueError):
        return None
    return min(seconds, 3600) if isfinite(seconds) and seconds >= 0 else None
