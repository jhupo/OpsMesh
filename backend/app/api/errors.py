from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette import status

from backend.app.core.errors import DomainError


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(DomainError, domain_exception_handler)


async def validation_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):
        raise exc

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={
            "error": {
                "code": "validation_error",
                "message": "Request validation failed",
                "request_id": getattr(request.state, "request_id", None),
                "details": _json_safe_validation_errors(exc.errors()),
            }
        },
    )


async def http_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    if not isinstance(exc, HTTPException):
        raise exc

    message = exc.detail if isinstance(exc.detail, str) else "Request failed"
    details = None
    if isinstance(exc.detail, dict):
        message = str(exc.detail.get("message") or message)
        details = {key: value for key, value in exc.detail.items() if key != "message"}
    return error_response(
        status_code=exc.status_code,
        code=_code_for_status(exc.status_code),
        message=message,
        request_id=getattr(request.state, "request_id", None),
        details=details,
        headers=exc.headers,
    )


async def domain_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    if not isinstance(exc, DomainError):
        raise exc
    return error_response(
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        request_id=getattr(request.state, "request_id", None),
        details=exc.details or None,
    )


def error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    request_id: str | None = None,
    details: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    payload = {
        "code": code,
        "message": message,
        "request_id": request_id,
    }
    if details is not None:
        payload["details"] = details
    return JSONResponse(
        status_code=status_code,
        content={"error": payload},
        headers=headers,
    )


def _code_for_status(status_code: int) -> str:
    match status_code:
        case status.HTTP_400_BAD_REQUEST:
            return "bad_request"
        case status.HTTP_401_UNAUTHORIZED:
            return "unauthorized"
        case status.HTTP_403_FORBIDDEN:
            return "forbidden"
        case status.HTTP_404_NOT_FOUND:
            return "not_found"
        case status.HTTP_409_CONFLICT:
            return "conflict"
        case status.HTTP_413_CONTENT_TOO_LARGE:
            return "payload_too_large"
        case status.HTTP_429_TOO_MANY_REQUESTS:
            return "rate_limited"
        case status.HTTP_503_SERVICE_UNAVAILABLE:
            return "service_unavailable"
        case _:
            return "http_error"


def _json_safe_validation_errors(errors: list[dict[str, object]]) -> list[dict[str, object]]:
    safe_errors: list[dict[str, object]] = []
    for error in errors:
        safe_error = dict(error)
        context = safe_error.get("ctx")
        if isinstance(context, dict):
            safe_error["ctx"] = {
                key: str(value) if isinstance(value, Exception) else value
                for key, value in context.items()
            }
        safe_errors.append(safe_error)
    return safe_errors
