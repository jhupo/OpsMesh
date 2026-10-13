"""Validate untrusted MCP diagnostics and separate public errors from operator evidence."""

from __future__ import annotations

import asyncio
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

DIAGNOSTIC_META_KEY = "com.opsmesh/diagnostic"


class ErrorLocation(BaseModel):
    model_config = ConfigDict(extra="ignore")
    location_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    line: int = Field(ge=0, le=10_000_000)


class ExceptionEvidence(BaseModel):
    model_config = ConfigDict(extra="ignore")
    exception_type: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_.]{0,119}$")
    frames: list[ErrorLocation] = Field(default_factory=list, max_length=16)


class OperatorEvidence(BaseModel):
    model_config = ConfigDict(extra="ignore")
    exceptions: list[ExceptionEvidence] = Field(default_factory=list, max_length=8)


class MCPErrorDiagnostic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    category: Literal[
        "network", "authentication", "rate_limit", "http", "protocol", "execution", "cancelled"
    ] = "execution"
    phase: Literal["login", "business_request", "connect", "tool", "control", "discovery"] = "tool"
    exception_type: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_.]{0,119}$")
    http_status: int | None = Field(default=None, ge=100, le=599)
    duration_ms: int | None = Field(default=None, ge=0, le=86_400_000)
    retryable: bool = False
    operation_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


def normalize_failure(
    value: object,
    *,
    code: str,
    message: str,
    error_id: UUID | None = None,
) -> dict[str, object]:
    payload = value if isinstance(value, dict) else {}
    try:
        diagnostic = MCPErrorDiagnostic.model_validate(payload, strict=True)
        evidence = OperatorEvidence.model_validate(payload.get("diagnostic", {}), strict=True)
    except ValidationError:
        diagnostic = MCPErrorDiagnostic(category="protocol")
        evidence = OperatorEvidence()
        available = False
    else:
        available = diagnostic.exception_type is not None or diagnostic.http_status is not None
    result: dict[str, object] = {
        **diagnostic.model_dump(mode="json"),
        "code": code,
        "message": message,
        "detail_available": available,
    }
    if error_id is not None:
        result["error_id"] = str(error_id)
    if evidence.exceptions:
        result["diagnostic"] = evidence.model_dump(mode="json", exclude_none=True)
    return result


def native_failure(exc: BaseException) -> dict[str, object]:
    """Retain bounded exception types and status codes without vendor strings or URLs."""
    exceptions: list[BaseException] = []
    pending = [exc]
    seen: set[int] = set()
    while pending and len(exceptions) < 8:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        exceptions.append(current)
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    status_code = next(
        (
            status
            for error in exceptions
            if isinstance(
                status := getattr(getattr(error, "response", None), "status_code", None), int
            )
            and 100 <= status <= 599
        ),
        None,
    )
    cancelled = isinstance(exc, asyncio.CancelledError)
    timed_out = any(
        isinstance(error, TimeoutError)
        or type(error).__name__
        in {"TimeoutException", "ReadTimeout", "ConnectTimeout", "PoolTimeout", "WriteTimeout"}
        for error in exceptions
    )
    return normalize_failure(
        {
            "exception_type": type(exceptions[-1]).__name__,
            "http_status": status_code,
            "category": (
                "cancelled"
                if cancelled
                else "authentication"
                if status_code in {401, 403}
                else "rate_limit"
                if status_code == 429
                else "http"
                if status_code is not None
                else "network"
                if timed_out
                else "execution"
            ),
            "retryable": timed_out
            or status_code == 429
            or (status_code is not None and status_code >= 500),
            "diagnostic": {
                "exceptions": [{"exception_type": type(error).__name__} for error in exceptions]
            },
        },
        code="mcp_native_call_cancelled" if cancelled else "mcp_native_call_failed",
        message="Native MCP call cancelled" if cancelled else "Native MCP call failed",
    )


def public_error(value: dict[str, object]) -> dict[str, object]:
    return {key: item for key, item in value.items() if key != "diagnostic"}


def response_diagnostic(response: dict[str, object]) -> object:
    metadata = response.get("_meta")
    return metadata.get(DIAGNOSTIC_META_KEY) if isinstance(metadata, dict) else None


def public_response(response: dict[str, object]) -> dict[str, object]:
    metadata = response.get("_meta")
    if not isinstance(metadata, dict) or DIAGNOSTIC_META_KEY not in metadata:
        return response
    remaining = {key: value for key, value in metadata.items() if key != DIAGNOSTIC_META_KEY}
    result = {key: value for key, value in response.items() if key != "_meta"}
    if remaining:
        result["_meta"] = remaining
    return result
