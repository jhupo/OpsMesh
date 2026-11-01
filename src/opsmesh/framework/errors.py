"""Stable application errors, separate from matcher control flow."""

from typing import Any
from uuid import uuid4

from pydantic import Field

from opsmesh.framework.typing import OpsMeshContract


class ErrorInfo(OpsMeshContract):
    error_id: str = Field(default_factory=lambda: str(uuid4()))
    code: str
    safe_message: str
    retryable: bool = False
    phase: str | None = None
    provider: str | None = None
    provider_code: str | None = None
    retry_after_seconds: float | None = Field(default=None, ge=0)
    details: dict[str, Any] = Field(default_factory=dict)


class OpsMeshError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
        phase: str | None = None,
        provider: str | None = None,
        provider_code: str | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.info = ErrorInfo(
            code=code.upper(),
            safe_message=message,
            retryable=retryable,
            details=details or {},
            phase=phase,
            provider=provider,
            provider_code=provider_code,
            retry_after_seconds=retry_after_seconds,
        )

    @property
    def code(self) -> str:
        return self.info.code

    @property
    def message(self) -> str:
        return self.info.safe_message

    @property
    def retryable(self) -> bool:
        return self.info.retryable

    @property
    def details(self) -> dict[str, Any]:
        return self.info.details
