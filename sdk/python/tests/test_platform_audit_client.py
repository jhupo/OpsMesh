"""Generated governance group contract tests: audit, approvals and security events."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

import httpx
import pytest

from opsmesh_sdk.api.platform import (
    ApprovalDecisionRequest,
    ExtensionAuditAppend,
    create_platform_client,
)
from opsmesh_sdk.client import PlatformClient
from opsmesh_sdk.errors import AuthorizationError
from opsmesh_sdk.transport.http import HttpTransport

EVENT_ID = UUID("11111111-2222-3333-4444-555555555555")
APPROVAL_ID = UUID("66666666-7777-8888-9999-aaaaaaaaaaaa")
APPROVER_ID = UUID("99999999-8888-7777-6666-555555555555")


def platform_client(handler: Any) -> Any:
    client = PlatformClient(
        "https://api.invalid",
        session_id="session-1",
        transport=HttpTransport(
            "https://api.invalid",
            session_id="session-1",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        ),
    )
    return create_platform_client(client)


def audit_event_payload(sequence: int) -> dict[str, Any]:
    return {
        "event_id": str(EVENT_ID),
        "sequence": sequence,
        "occurred_at": "2026-10-16T00:00:00Z",
        "recorded_at": "2026-10-16T00:00:00Z",
        "category": "operation",
        "severity": "info",
        "schema_version": 1,
        "operation_id": None,
        "correlation_id": None,
        "causation_id": None,
        "trace_id": None,
        "actor_type": "user",
        "actor_id": None,
        "configuration_id": None,
        "installation_id": None,
        "instance_id": None,
        "invocation_id": None,
        "package_key": None,
        "configuration_revision": None,
        "action": "user.created",
        "target_type": "user",
        "target_id": "target",
        "summary": "Account opened",
        "details": {},
        "evidence_refs": [],
        "source": "platform",
    }


def test_audit_list_uses_the_occurrence_cursor_and_filters() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(
            200,
            json={"events": [audit_event_payload(1)], "next_after_sequence": 2},
        )

    page = asyncio.run(
        platform_client(handler).audit.list(after_sequence=1, action="user.created")
    )
    assert captured["params"]["after_sequence"] == "1"
    assert captured["params"]["action"] == "user.created"
    assert page.next_after_sequence == 2
    assert page.events[0].sequence == 1


def test_audit_record_keeps_the_idempotency_key_and_omits_unset_details() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json={"event_id": str(EVENT_ID), "replayed": False})

    receipt = asyncio.run(
        platform_client(handler).audit.record(
            ExtensionAuditAppend(
                action="invocation.completed",
                target_type="invocation",
                target_id="target",
                summary="Invocation finished",
                idempotency_key="audit-key-0001",
            )
        )
    )
    assert captured["body"] == {
        "action": "invocation.completed",
        "target_type": "invocation",
        "target_id": "target",
        "summary": "Invocation finished",
        "idempotency_key": "audit-key-0001",
    }
    assert receipt.event_id == EVENT_ID
    assert receipt.replayed is False


def test_approval_decision_carries_its_idempotency_key() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "approval_id": str(APPROVAL_ID),
                "kind": "capability.publish",
                "status": "approved",
                "title": "Publish",
                "summary": "summary",
                "details": {},
                "target_type": "capability_version",
                "target_id": "target",
                "target_version": None,
                "approver_user_id": str(APPROVER_ID),
                "approver_role": None,
                "request_key": "attempt-1",
                "requester_user_id": None,
                "expires_at": None,
                "created_at": "2026-10-16T00:00:00Z",
                "decided_at": "2026-10-16T00:01:00Z",
                "decided_by_user_id": None,
                "decision_reason": "ok",
            },
        )

    decision = asyncio.run(
        platform_client(handler).approvals.approve(
            APPROVAL_ID,
            ApprovalDecisionRequest(idempotency_key="approval-key-01", reason="ok"),
        )
    )
    assert captured["path"] == f"/api/v1/platform/approvals/{APPROVAL_ID}/approve"
    assert captured["body"] == {"idempotency_key": "approval-key-01", "reason": "ok"}
    assert decision.status == "approved"


def test_forbidden_decision_maps_to_the_authorization_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={
                "error": {
                    "code": "permission_denied",
                    "message": "This action requires a platform role",
                }
            },
        )

    with pytest.raises(AuthorizationError):
        asyncio.run(
            platform_client(handler).approvals.approve(
                APPROVAL_ID,
                ApprovalDecisionRequest(idempotency_key="approval-key-02"),
            )
        )


def test_security_event_list_filters_server_side() -> None:
    captured = {}

    def handler(request):
        captured.update(dict(request.url.params))
        event = audit_event_payload(1)
        event.update(category="security", severity="warning", action="login.invalid_credentials")
        return httpx.Response(200, json={"events": [event], "next_after_sequence": None})

    page = asyncio.run(
        platform_client(handler).audit.list(category="security", severity="warning")
    )
    assert captured == {"category": "security", "severity": "warning", "limit": "50"}
    assert page.events[0].action == "login.invalid_credentials"
