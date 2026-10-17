"""Generated workspaces group contract tests: tenants, members and credentials."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

import httpx

from opsmesh_sdk.api.workspaces import (
    CredentialCreateRequest,
    MemberRoleUpdateRequest,
    WorkspaceCreateRequest,
    create_workspaces_client,
)
from opsmesh_sdk.client import PlatformClient
from opsmesh_sdk.transport.http import HttpTransport

WORKSPACE_ID = UUID("11111111-2222-3333-4444-555555555555")
USER_ID = UUID("66666666-7777-8888-9999-000000000000")


def workspaces_client(handler: Any) -> Any:
    client = PlatformClient(
        "https://api.invalid",
        session_id="session-1",
        transport=HttpTransport(
            "https://api.invalid",
            session_id="session-1",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        ),
    )
    return create_workspaces_client(client)


def workspace_payload() -> dict[str, Any]:
    return {
        "workspace_id": str(WORKSPACE_ID),
        "name": "Acme",
        "description": "",
        "status": "active",
        "created_by_user_id": str(USER_ID),
        "created_at": "2026-10-17T00:00:00Z",
        "updated_at": "2026-10-17T00:00:00Z",
    }


def test_workspace_create_posts_the_tenant_metadata() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=workspace_payload())

    group = workspaces_client(handler)
    result = asyncio.run(group.create(WorkspaceCreateRequest(name="Acme")))
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/v1/workspaces"
    assert captured["body"]["name"] == "Acme"
    assert result.workspace_id == WORKSPACE_ID
    assert result.status == "active"


def test_member_role_update_patches_the_space_role() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "workspace_id": str(WORKSPACE_ID),
                "user_id": str(USER_ID),
                "role": "admin",
                "created_at": "2026-10-17T00:00:00Z",
                "updated_at": "2026-10-17T00:00:00Z",
            },
        )

    group = workspaces_client(handler)
    result = asyncio.run(
        group.members.update_role(
            WORKSPACE_ID, USER_ID, MemberRoleUpdateRequest(role="admin")
        )
    )
    assert captured["method"] == "PATCH"
    assert captured["path"] == f"/api/v1/workspaces/{WORKSPACE_ID}/members/{USER_ID}"
    assert captured["body"]["role"] == "admin"
    assert result.role == "admin"


def test_capacity_read_returns_every_kind() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        return httpx.Response(
            200,
            json={
                "workspace_id": str(WORKSPACE_ID),
                "kinds": [
                    {"kind": "concurrency", "limit_value": 4, "reserved_value": 1},
                    {"kind": "environment", "limit_value": None, "reserved_value": 0},
                ],
            },
        )

    group = workspaces_client(handler)
    snapshot = asyncio.run(group.capacity.read(WORKSPACE_ID))
    assert captured["path"] == f"/api/v1/workspaces/{WORKSPACE_ID}/capacity"
    assert [kind.kind for kind in snapshot.kinds] == ["concurrency", "environment"]
    assert snapshot.kinds[0].reserved_value == 1


def test_credential_create_never_echoes_the_secret() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            201,
            json={
                "credential_id": str(USER_ID),
                "workspace_id": str(WORKSPACE_ID),
                "name": "openai-key",
                "kind": "model_provider",
                "fingerprint": "sha256:abc",
                "key_id": "local",
                "revision": 1,
                "status": "active",
                "created_by_user_id": str(USER_ID),
                "created_at": "2026-10-17T00:00:00Z",
                "updated_at": "2026-10-17T00:00:00Z",
                "revoked_at": None,
            },
        )

    group = workspaces_client(handler)
    result = asyncio.run(
        group.credentials.create(
            WORKSPACE_ID,
            CredentialCreateRequest(
                name="openai-key", kind="model_provider", secret={"api_key": "sk-test"}
            ),
        )
    )
    assert captured["body"]["secret"] == {"api_key": "sk-test"}
    assert result.fingerprint == "sha256:abc"
    assert not hasattr(result, "secret")


def test_received_invitations_list_the_current_user_inbox() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        return httpx.Response(200, json={"invitations": [], "total": 0})

    group = workspaces_client(handler)
    page = asyncio.run(group.invitations.received())
    assert captured["path"] == "/api/v1/workspaces/invitations/received"
    assert page.total == 0
