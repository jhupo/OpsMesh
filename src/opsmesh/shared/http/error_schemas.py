"""Public error envelope used by the HTTP exception handlers."""

from pydantic import BaseModel


class ErrorInfo(BaseModel):
    code: str
    message: str
    request_id: str | None
    details: dict[str, object] | list[dict[str, object]] | None = None


class ErrorResponse(BaseModel):
    error: ErrorInfo
