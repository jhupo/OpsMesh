"""Generated orchestration contracts: private model sessions and native history."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

import httpx
import pytest

from opsmesh_sdk.api.orchestration import (
    HistoryAppendRequest,
    SessionCreateRequest,
    create_orchestration_client,
)
from opsmesh_sdk.client import PlatformClient
from opsmesh_sdk.errors import ConflictError, NotFoundError, UnknownEffectError
from opsmesh_sdk.transport.http import HttpTransport

SESSION_ID = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
SCOPE_ID = UUID("11111111-2222-3333-4444-555555555555")
PRINCIPAL_ID = UUID("66666666-7777-8888-9999-aaaaaaaaaaaa")
CONFIGURATION_ID = UUID("12121212-3434-5656-7878-909090909090")
CREATED_AT = "2026-10-17T00:00:00Z"


def execution_client(handler: Any) -> Any:
    client = PlatformClient(
        "https://api.invalid",
        session_id="session-1",
        transport=HttpTransport(
            "https://api.invalid",
            session_id="session-1",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        ),
    )
    return create_orchestration_client(client)


def session_payload(status: str = "open") -> dict[str, Any]:
    return {
        "session_id": str(SESSION_ID),
        "scope_id": str(PRINCIPAL_ID),
        "configuration_id": str(CONFIGURATION_ID),
        "principal_id": str(PRINCIPAL_ID),
        "owner_ref": f"user:{PRINCIPAL_ID}",
        "status": status,
        "created_at": CREATED_AT,
        "updated_at": CREATED_AT,
    }


def test_create_session_opens_in_the_named_configuration_scope() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["method"] = request.method
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=session_payload())

    session = asyncio.run(
        execution_client(handler).sessions.create(
            SessionCreateRequest(configuration_id=CONFIGURATION_ID)
        )
    )
    assert captured["path"] == "/api/v1/orchestration/sessions"
    assert captured["method"] == "POST"
    assert captured["body"] == {"configuration_id": str(CONFIGURATION_ID)}
    assert session.session_id == SESSION_ID
    assert session.status == "open"
    assert session.owner_ref == f"user:{PRINCIPAL_ID}"
    assert session.configuration_id == CONFIGURATION_ID


def test_get_session_reads_the_stored_view() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        return httpx.Response(200, json=session_payload(status="closing"))

    session = asyncio.run(execution_client(handler).sessions.get(SESSION_ID))
    assert captured["path"] == f"/api/v1/orchestration/sessions/{SESSION_ID}"
    assert session.status == "closing"


def test_close_session_reports_the_drained_status() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["method"] = request.method
        return httpx.Response(200, json=session_payload(status="closed"))

    session = asyncio.run(execution_client(handler).sessions.close(SESSION_ID))
    assert captured["path"] == f"/api/v1/orchestration/sessions/{SESSION_ID}/close"
    assert captured["method"] == "POST"
    assert session.status == "closed"


def test_delete_session_sends_the_delete_without_a_body() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["method"] = request.method
        captured["content"] = request.content
        return httpx.Response(204)

    result = asyncio.run(execution_client(handler).sessions.delete(SESSION_ID))
    assert result is None
    assert captured["path"] == f"/api/v1/orchestration/sessions/{SESSION_ID}"
    assert captured["method"] == "DELETE"
    assert captured["content"] == b""


def test_history_list_reads_the_bounded_page_oldest_first() -> None:
    captured: dict[str, Any] = {}
    item = {"role": "user", "content": "hello"}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"items": [item]})

    page = asyncio.run(execution_client(handler).sessions.history.list(SESSION_ID, limit=50))
    assert captured["path"] == f"/api/v1/orchestration/sessions/{SESSION_ID}/history"
    assert captured["params"]["limit"] == "50"
    assert page.items == [item]


def test_history_append_carries_the_items_and_the_idempotency_key() -> None:
    captured: dict[str, Any] = {}
    items = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["params"] = dict(request.url.params)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"appended": 2})

    view = asyncio.run(
        execution_client(handler).sessions.history.append(
            SESSION_ID,
            HistoryAppendRequest(items=items),
            idempotency_key="append-key-0001",
        )
    )
    assert captured["path"] == f"/api/v1/orchestration/sessions/{SESSION_ID}/history/items"
    assert captured["params"]["idempotency_key"] == "append-key-0001"
    assert captured["body"] == {"items": items}
    assert view.appended == 2


def test_history_pop_returns_the_removed_item() -> None:
    captured: dict[str, Any] = {}
    item = {"role": "assistant", "content": "hi"}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["method"] = request.method
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"item": item})

    view = asyncio.run(
        execution_client(handler).sessions.history.pop(SESSION_ID, idempotency_key="pop-key-0001")
    )
    assert captured["path"] == f"/api/v1/orchestration/sessions/{SESSION_ID}/history/pop"
    assert captured["method"] == "POST"
    assert captured["params"]["idempotency_key"] == "pop-key-0001"
    assert view.item == item


def test_history_pop_reports_an_empty_history() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"item": None})

    view = asyncio.run(
        execution_client(handler).sessions.history.pop(SESSION_ID, idempotency_key="pop-key-0002")
    )
    assert view.item is None


def test_history_clear_acknowledges_without_a_body() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["method"] = request.method
        captured["params"] = dict(request.url.params)
        return httpx.Response(204)

    result = asyncio.run(
        execution_client(handler).sessions.history.clear(
            SESSION_ID, idempotency_key="clear-key-0001"
        )
    )
    assert result is None
    assert captured["path"] == f"/api/v1/orchestration/sessions/{SESSION_ID}/history/clear"
    assert captured["method"] == "POST"
    assert captured["params"]["idempotency_key"] == "clear-key-0001"


def test_a_missing_session_maps_to_the_not_found_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={"error": {"code": "not_found", "message": "The session is not visible"}},
        )

    with pytest.raises(NotFoundError):
        asyncio.run(execution_client(handler).sessions.get(SESSION_ID))


def test_a_blocked_close_maps_to_the_conflict_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={"error": {"code": "session_blocked", "message": "The session is blocked"}},
        )

    with pytest.raises(ConflictError):
        asyncio.run(execution_client(handler).sessions.close(SESSION_ID))


def test_an_uncertain_history_operation_maps_to_the_unknown_effect_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={
                "error": {
                    "code": "history_operation_uncertain",
                    "message": "The history operation outcome is unknown",
                }
            },
        )

    with pytest.raises(UnknownEffectError):
        asyncio.run(
            execution_client(handler).sessions.history.pop(
                SESSION_ID, idempotency_key="pop-key-0003"
            )
        )
