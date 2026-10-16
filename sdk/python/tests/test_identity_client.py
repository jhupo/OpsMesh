"""Generated identity group contract tests: routes, payloads, DTOs and errors."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

import httpx
import pytest

from opsmesh_sdk.api.identity import (
    LoginRequest,
    UpdateProfileRequest,
    create_identity_client,
)
from opsmesh_sdk.client import PlatformClient
from opsmesh_sdk.errors import AuthenticationError, NotFoundError
from opsmesh_sdk.transport.contracts import SESSION_HEADER
from opsmesh_sdk.transport.http import HttpTransport

USER_ID = UUID("11111111-2222-3333-4444-555555555555")
SESSION_ID = UUID("66666666-7777-8888-9999-aaaaaaaaaaaa")


def platform_client(handler: Any) -> PlatformClient:
    return PlatformClient(
        "https://api.invalid",
        session_id="session-1",
        transport=HttpTransport(
            "https://api.invalid",
            session_id="session-1",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        ),
    )


def identity_client(handler: Any) -> Any:
    return create_identity_client(platform_client(handler))


def profile_payload() -> dict[str, Any]:
    return {
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


def test_the_composing_client_creates_the_identity_group() -> None:
    client = platform_client(lambda request: httpx.Response(200, json={}))
    group = client.group("identity")
    assert type(group).__name__ == "IdentityClient"
    assert client.group("identity") is group


def test_login_posts_credentials_and_parses_the_grant() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["session"] = request.headers.get(SESSION_HEADER)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "session_token": str(SESSION_ID),
                "expires_at": "2026-11-15T00:00:00Z",
                "user": {
                    "user_id": str(USER_ID),
                    "email": "user@example.com",
                    "display_name": "User",
                    "status": "active",
                    "platform_roles": ["member"],
                },
            },
        )

    grant = asyncio.run(
        identity_client(handler).auth.login(
            LoginRequest(email="user@example.com", password="correct-horse")
        )
    )
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/v1/auth/login"
    assert captured["session"] == "session-1"
    assert captured["body"] == {"email": "user@example.com", "password": "correct-horse"}
    assert grant.session_token == SESSION_ID
    assert grant.user.user_id == USER_ID
    assert grant.user.platform_roles == ["member"]


def test_optional_profile_fields_are_omitted_never_nulled() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        payload = profile_payload()
        payload["display_name"] = "Renamed"
        return httpx.Response(200, json=payload)

    profile = asyncio.run(
        identity_client(handler).users.update_current(
            UpdateProfileRequest(display_name="Renamed")
        )
    )
    assert captured["body"] == {"display_name": "Renamed"}
    assert profile.display_name == "Renamed"


def test_list_users_sends_query_parameters_and_parses_the_page() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(
            200, json={"users": [profile_payload()], "total": 1}
        )

    page = asyncio.run(
        identity_client(handler).users.list(status="active", email_query="user")
    )
    assert captured["params"]["status"] == "active"
    assert captured["params"]["email_query"] == "user"
    assert captured["params"]["limit"] == "50"
    assert page.total == 1
    assert page.users[0].user_id == USER_ID


def test_sessions_list_parses_an_array_answer() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/auth/sessions"
        return httpx.Response(
            200,
            json=[
                {
                    "session_id": str(SESSION_ID),
                    "expires_at": "2026-11-15T00:00:00Z",
                    "revoked_at": None,
                }
            ],
        )

    sessions = asyncio.run(identity_client(handler).auth.sessions.list())
    assert sessions[0].session_id == SESSION_ID
    assert sessions[0].revoked_at is None


def test_path_parameters_reach_the_request_url() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        return httpx.Response(200, json=profile_payload())

    asyncio.run(identity_client(handler).users.read(USER_ID))
    assert captured["path"] == f"/api/v1/users/{USER_ID}"


def test_empty_answers_carry_no_response_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        return httpx.Response(204)

    result = asyncio.run(
        identity_client(handler).auth.sessions.revoke(SESSION_ID)
    )
    assert result is None


def test_avatar_upload_sends_raw_bytes_with_the_declared_content_type() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["content_type"] = request.headers.get("content-type")
        captured["content"] = request.content
        return httpx.Response(204)

    asyncio.run(
        identity_client(handler).users.avatar.upload(
            b"\x89PNG\r\n\x1a\nrest", content_type="image/png"
        )
    )
    assert captured["method"] == "PUT"
    assert captured["content_type"] == "image/png"
    assert captured["content"] == b"\x89PNG\r\n\x1a\nrest"


def test_avatar_read_returns_the_raw_bytes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"\x89PNG\r\n\x1a\nrest", headers={"content-type": "image/png"}
        )

    content = asyncio.run(identity_client(handler).users.avatar.read())
    assert content == b"\x89PNG\r\n\x1a\nrest"


def test_login_failure_maps_to_the_typed_authentication_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={
                "error": {
                    "code": "invalid_credentials",
                    "message": "Invalid credentials",
                    "request_id": "req-1",
                }
            },
        )

    with pytest.raises(AuthenticationError) as excinfo:
        asyncio.run(
            identity_client(handler).auth.login(
                LoginRequest(email="user@example.com", password="wrong")
            )
        )
    assert excinfo.value.request_id == "req-1"


def test_missing_profile_maps_to_the_typed_not_found_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404, json={"error": {"code": "not_found", "message": "The user does not exist"}}
        )

    with pytest.raises(NotFoundError):
        asyncio.run(identity_client(handler).users.read(USER_ID))
