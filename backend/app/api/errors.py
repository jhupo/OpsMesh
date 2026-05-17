from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette import status


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)


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
                "details": exc.errors(),
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
    return error_response(
        status_code=exc.status_code,
        code=_code_for_status(exc.status_code),
        message=message,
        request_id=getattr(request.state, "request_id", None),
    )


def error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    request_id: str | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "request_id": request_id,
            }
        },
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
        case _:
            return "http_error"
