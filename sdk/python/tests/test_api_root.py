"""Runtime API root tests: generated clients honor the configured prefix.

The server serves its features under ``OPSMESH_API_PREFIX``; the generated
clients carry relative resource paths and prepend the channel's runtime API
root, so one generated client reaches a server with any configured prefix.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import httpx
import pytest

from opsmesh_sdk.api.identity import create_identity_client
from opsmesh_sdk.client import PlatformClient, validated_api_root
from opsmesh_sdk.errors import ValidationError
from opsmesh_sdk.transport.http import HttpTransport

USER_ID = UUID("11111111-2222-3333-4444-555555555555")

PROFILE_PAYLOAD: dict[str, Any] = {
    "user_id": str(USER_ID),
    "email": "user@example.com",
    "display_name": "User",
    "status": "active",
    "platform_roles": ["member"],
    "work_root": None,
    "has_avatar": False,
    "avatar_content_type": None,
    "avatar_updated_at": None,
    "created_at": "2026-10-16T00:00:00Z",
    "updated_at": "2026-10-16T00:00:00Z",
}


def asgi_app(seen: list[str]):
    """A minimal ASGI application that records the path it was served."""

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        seen.append(scope["path"])
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        body = httpx.Response(200, json=PROFILE_PAYLOAD).content
        await send({"type": "http.response.body", "body": body})

    return app


def client_over(app: Any, *, api_prefix: str) -> PlatformClient:
    return PlatformClient(
        "http://opsmesh.test",
        session_id="session-1",
        api_prefix=api_prefix,
        transport=HttpTransport(
            "http://opsmesh.test",
            session_id="session-1",
            client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app)),
        ),
    )


def test_the_default_api_root_reaches_the_documented_prefix() -> None:
    seen: list[str] = []
    client = client_over(asgi_app(seen), api_prefix="/api/v1")
    group = client.group("identity")
    profile = asyncio.run(group.users.read_current())  # type: ignore[attr-defined]
    assert seen == ["/api/v1/users/me"]
    assert profile.user_id == USER_ID


def test_a_configured_api_root_reaches_a_server_with_another_prefix() -> None:
    """OPSMESH_API_PREFIX on the server must not break the generated client."""
    seen: list[str] = []
    client = client_over(asgi_app(seen), api_prefix="/custom/v1")
    profile = asyncio.run(client.group("identity").users.read_current())  # type: ignore[attr-defined]
    assert seen == ["/custom/v1/users/me"]
    assert profile.email == "user@example.com"


def test_a_path_parameter_combines_with_the_runtime_api_root() -> None:
    seen: list[str] = []
    client = client_over(asgi_app(seen), api_prefix="/gateway/api")
    group = create_identity_client(client)
    asyncio.run(group.auth.sessions.revoke(USER_ID))
    assert seen == [f"/gateway/api/auth/sessions/{USER_ID}"]


@pytest.mark.parametrize("prefix", ["api/v1", "/api/v1/", "/"])
def test_an_unusable_api_root_is_refused(prefix: str) -> None:
    with pytest.raises(ValidationError):
        validated_api_root(prefix)
