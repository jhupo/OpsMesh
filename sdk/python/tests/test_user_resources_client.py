"""Generated administrator model grants and user quantity allowance contracts."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

import httpx
import pytest

from opsmesh_sdk.api.identity import QuotaLimitsRequest, create_identity_client
from opsmesh_sdk.api.platform import (
    ModelConnectionCreateRequest,
    ModelGrantRequest,
    create_platform_client,
)
from opsmesh_sdk.client import PlatformClient
from opsmesh_sdk.errors import RateLimitError
from opsmesh_sdk.transport.http import HttpTransport

USER_ID = UUID("66666666-7777-8888-9999-000000000000")
CONNECTION_ID = UUID("aaaaaaaa-bbbb-cccc-dddd-000000000000")


def client(handler: Any) -> PlatformClient:
    return PlatformClient(
        "https://api.invalid",
        session_id="session-1",
        transport=HttpTransport(
            "https://api.invalid",
            session_id="session-1",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        ),
    )


def test_capacity_read_is_scoped_to_the_authenticated_user() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/identity/me/capacity"
        return httpx.Response(
            200,
            json={
                "user_id": str(USER_ID),
                "kinds": [
                    {"kind": "concurrency", "limit_value": 4, "reserved_value": 1},
                    {"kind": "environment", "limit_value": None, "reserved_value": 0},
                ],
            },
        )

    snapshot = asyncio.run(create_identity_client(client(handler)).quotas.read_current_capacity())
    assert snapshot.user_id == str(USER_ID)
    assert snapshot.kinds[0].reserved_value == 1


def test_capacity_rejection_uses_the_quantity_limit_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={
                "error": {
                    "code": "capacity_exceeded",
                    "message": "User execution capacity is exhausted",
                    "request_id": "capacity-check",
                }
            },
        )

    with pytest.raises(RateLimitError) as failure:
        asyncio.run(create_identity_client(client(handler)).quotas.read_current_capacity())
    assert failure.value.request_id == "capacity-check"


def test_model_create_keeps_key_out_of_the_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/platform/model-connections"
        assert json.loads(request.content)["api_key"] == "test-secret"
        return httpx.Response(
            201,
            json={
                "connection_id": str(CONNECTION_ID),
                "name": "model-key",
                "provider": "openai",
                "base_url": "http://localhost:8080/v1",
                "default_model": "model-a",
                "models": ["model-a"],
                "revision": 1,
                "status": "active",
                "last_probe_status": "never",
                "last_probe_detail": None,
                "last_probe_at": None,
                "created_at": "2026-10-19T00:00:00Z",
                "updated_at": "2026-10-19T00:00:00Z",
            },
        )

    result = asyncio.run(
        create_platform_client(client(handler)).model_connections.create(
            ModelConnectionCreateRequest(
                name="model-key",
                api_key="test-secret",
                base_url="http://localhost:8080/v1",
                default_model="model-a",
                models=["model-a"],
            )
        )
    )
    assert result.connection_id == CONNECTION_ID
    assert result.models == ["model-a"]
    assert not hasattr(result, "api_key")
    assert not hasattr(result, "encrypted_key")


def test_administrator_grants_an_explicit_model_subset_to_user() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert request.url.path == (
            f"/api/v1/platform/model-connections/{CONNECTION_ID}/users/{USER_ID}"
        )
        assert json.loads(request.content) == {"models": ["model-a"], "is_default": True}
        return httpx.Response(
            200,
            json={
                "connection_id": str(CONNECTION_ID),
                "user_id": str(USER_ID),
                "models": ["model-a"],
                "is_default": True,
            },
        )

    result = asyncio.run(
        create_platform_client(client(handler)).model_connections.grant(
            CONNECTION_ID, USER_ID, ModelGrantRequest(models=["model-a"], is_default=True)
        )
    )
    assert result.user_id == USER_ID
    assert result.models == ["model-a"]


def test_administrator_sets_quantity_allowances_without_price_or_budget_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert request.url.path == f"/api/v1/identity/users/{USER_ID}/quotas"
        assert json.loads(request.content) == {"token_limit": 1000, "storage_limit_bytes": 4096}
        return httpx.Response(
            200,
            json={
                "token_limit": 1000,
                "used_tokens": 20,
                "reserved_tokens": 10,
                "available_tokens": 970,
                "storage_limit_bytes": 4096,
                "used_bytes": 0,
                "reserved_bytes": 0,
                "available_bytes": 4096,
            },
        )

    result = asyncio.run(
        create_identity_client(client(handler)).quotas.update(
            USER_ID, QuotaLimitsRequest(token_limit=1000, storage_limit_bytes=4096)
        )
    )
    assert result.available_tokens == 970
