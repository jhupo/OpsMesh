"""Safe content responses: verified bytes served as private downloads."""

from __future__ import annotations

from fastapi import Response

DEFAULT_MEDIA_TYPE = "application/octet-stream"


def content_download_response(
    payload: bytes, *, filename: str, media_type: str | None
) -> Response:
    """Serve verified content bytes as a private download."""
    return Response(
        content=payload,
        media_type=media_type or DEFAULT_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{_safe_filename(filename)}"',
            "Cache-Control": "no-store",
        },
    )


def _safe_filename(filename: str) -> str:
    """A download name may never break out of the quoted header value."""
    cleaned = filename.replace('"', "").replace("\\", "").replace("\r", "").replace("\n", "")
    return cleaned or "download"
