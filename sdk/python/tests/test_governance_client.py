"""Generated governance group contract tests: audit, approvals and security events."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

import httpx
import pytest

from opsmesh_sdk.api.governance import (
    ApprovalDecisionRequest,
    AuditVerificationRequest,
    BudgetUpsertRequest,
    ExtensionAuditAppend,
    PricingVersionPublishRequest,
    create_governance_client,
)
from opsmesh_sdk.client import PlatformClient
from opsmesh_sdk.errors import AuthorizationError, RateLimitError
from opsmesh_sdk.transport.http import HttpTransport

EVENT_ID = UUID("11111111-2222-3333-4444-555555555555")
APPROVAL_ID = UUID("66666666-7777-8888-9999-aaaaaaaaaaaa")
APPROVER_ID = UUID("99999999-8888-7777-6666-555555555555")
VERIFICATION_ID = UUID("bbbbbbbb-cccc-dddd-eeee-ffff00001111")
WORKSPACE_ID = UUID("12121212-3434-5656-7878-909090909090")
PRICING_VERSION_ID = UUID("abcdabcd-1111-2222-3333-444444444444")
BUDGET_ID = UUID("deadbeef-1111-2222-3333-444444444444")


def governance_client(handler: Any) -> Any:
    client = PlatformClient(
        "https://api.invalid",
        session_id="session-1",
        transport=HttpTransport(
            "https://api.invalid",
            session_id="session-1",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        ),
    )
    return create_governance_client(client)


def audit_event_payload(sequence: int) -> dict[str, Any]:
    return {
        "event_id": str(EVENT_ID),
        "sequence": sequence,
        "occurred_at": "2026-10-16T00:00:00Z",
        "actor_type": "user",
        "actor_id": None,
        "workspace_id": None,
        "project_id": None,
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
        "previous_hash": "0" * 64,
        "event_hash": "1" * 64,
    }


def test_audit_list_uses_the_chain_cursor_and_filters() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(
            200,
            json={"events": [audit_event_payload(1)], "next_after_sequence": 2},
        )

    page = asyncio.run(
        governance_client(handler).audit.events.list(after_sequence=1, action="user.created")
    )
    assert captured["params"]["after_sequence"] == "1"
    assert captured["params"]["action"] == "user.created"
    assert page.next_after_sequence == 2
    assert page.events[0].sequence == 1
    assert page.events[0].event_hash == "1" * 64


def test_audit_record_keeps_the_idempotency_key_and_omits_unset_details() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            201, json={"event_id": str(EVENT_ID), "replayed": False}
        )

    receipt = asyncio.run(
        governance_client(handler).audit.events.record(
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


def test_verification_create_parses_the_verification_view() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/governance/audit/verifications"
        return httpx.Response(
            201,
            json={
                "verification_id": str(VERIFICATION_ID),
                "status": "running",
                "from_sequence": 1,
                "to_sequence": 10,
                "checked_count": 0,
                "first_broken_sequence": None,
                "failure_reason": None,
                "created_at": "2026-10-16T00:00:00Z",
            },
        )

    verification = asyncio.run(
        governance_client(handler).audit.verifications.create(
            AuditVerificationRequest(from_sequence=1, to_sequence=10)
        )
    )
    assert verification.verification_id == VERIFICATION_ID
    assert verification.status == "running"


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
                "requester_user_id": None,
                "expires_at": None,
                "created_at": "2026-10-16T00:00:00Z",
                "decided_at": "2026-10-16T00:01:00Z",
                "decided_by_user_id": None,
                "decision_reason": "ok",
            },
        )

    decision = asyncio.run(
        governance_client(handler).approvals.approve(
            APPROVAL_ID,
            ApprovalDecisionRequest(idempotency_key="approval-key-01", reason="ok"),
        )
    )
    assert captured["path"] == f"/api/v1/governance/approvals/{APPROVAL_ID}/approve"
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
            governance_client(handler).approvals.approve(
                APPROVAL_ID,
                ApprovalDecisionRequest(idempotency_key="approval-key-02"),
            )
        )


def test_security_event_list_filters_server_side() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(
            200,
            json={
                "events": [
                    {
                        "event_id": str(EVENT_ID),
                        "kind": "login.invalid_credentials",
                        "severity": "warning",
                        "summary": "Login attempt with invalid credentials",
                        "details": {},
                        "user_id": None,
                        "occurred_at": "2026-10-16T00:00:00Z",
                        "created_at": "2026-10-16T00:00:00Z",
                    }
                ],
                "total": 1,
            },
        )

    page = asyncio.run(
        governance_client(handler).security_events.list(severity="warning")
    )
    assert captured["params"]["severity"] == "warning"
    assert page.events[0].kind == "login.invalid_credentials"


def pricing_version_payload() -> dict[str, Any]:
    return {
        "pricing_version_id": str(PRICING_VERSION_ID),
        "subject_kind": "model",
        "subject_key": "gpt-test-model",
        "version": 1,
        "prices": {
            "currency": "USD",
            "input_price": "0.001",
            "output_price": "0.002",
            "cached_input_price": "0.0001",
            "tool_unit_price": None,
        },
        "digest": "a" * 64,
        "status": "active",
        "published_by_user_id": None,
        "created_at": "2026-10-16T00:00:00Z",
    }


def budget_payload() -> dict[str, Any]:
    return {
        "budget_id": str(BUDGET_ID),
        "workspace_id": str(WORKSPACE_ID),
        "currency": "USD",
        "budget_state": "limited",
        "limit_amount": "1.5",
        "unlimited": False,
        "period": "monthly",
        "enforcement": "block",
        "revision": 2,
        "current_window_key": "2026-10",
        "window_used": "0.200000000000",
        "window_reserved": "0.000000000000",
        "unlimited_decided_by_user_id": None,
        "unlimited_decided_at": None,
        "updated_at": "2026-10-16T00:00:00Z",
    }


def test_pricing_publish_and_list_round_trip_the_price_sheet() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["method"] = request.method
        captured["params"] = dict(request.url.params)
        if request.content:
            captured["body"] = json.loads(request.content)
            return httpx.Response(201, json=pricing_version_payload())
        return httpx.Response(
            200,
            json={"versions": [pricing_version_payload()], "total": 1},
        )

    client = governance_client(handler)
    published = asyncio.run(
        client.costs.pricing.publish(
            PricingVersionPublishRequest(
                subject_kind="model",
                subject_key="gpt-test-model",
                prices={
                    "currency": "USD",
                    "input_price": "0.001",
                    "output_price": "0.002",
                    "cached_input_price": "0.0001",
                },
            )
        )
    )
    assert captured["path"] == "/api/v1/governance/costs/pricing/versions"
    assert captured["method"] == "POST"
    assert captured["body"]["prices"]["input_price"] == "0.001"
    assert published.version == 1
    assert published.prices.output_price == "0.002"

    listed = asyncio.run(
        client.costs.pricing.list(subject_kind="model", subject_key="gpt-test-model")
    )
    assert captured["params"]["subject_kind"] == "model"
    assert captured["params"]["subject_key"] == "gpt-test-model"
    assert listed.total == 1
    assert listed.versions[0].digest == "a" * 64


def test_budget_upsert_carries_the_expected_revision() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["method"] = request.method
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=budget_payload())

    budget = asyncio.run(
        governance_client(handler).costs.budget.upsert(
            WORKSPACE_ID,
            BudgetUpsertRequest(
                currency="USD", limit_amount="1.5", expected_version=1
            ),
        )
    )
    assert captured["path"] == f"/api/v1/governance/costs/workspaces/{WORKSPACE_ID}/budget"
    assert captured["method"] == "PUT"
    assert captured["body"] == {
        "currency": "USD",
        "limit_amount": "1.5",
        "unlimited": False,
        "period": "monthly",
        "enforcement": "block",
        "expected_version": 1,
    }
    assert budget.budget_state == "limited"
    assert budget.revision == 2


def test_budget_read_queries_the_currency() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=budget_payload())

    budget = asyncio.run(
        governance_client(handler).costs.budget.read(WORKSPACE_ID, currency="USD")
    )
    assert captured["params"]["currency"] == "USD"
    assert budget.window_used == "0.200000000000"


def test_usage_and_reservations_pages_parse_the_money_strings() -> None:
    usage_payload_page = {
        "usages": [
            {
                "operation_id": "op-1",
                "subject_kind": "model",
                "subject_key": "gpt-test-model",
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cached_input_tokens": 0,
                    "tool_invocations": 0,
                },
                "actual_cost": "0.200000000000",
                "overage_amount": "0.000000000000",
                "pricing_version_id": str(PRICING_VERSION_ID),
                "recorded_at": "2026-10-16T00:00:00Z",
            }
        ],
        "total": 1,
    }
    reservations_payload_page = {
        "reservations": [
            {
                "operation_id": "op-2",
                "workspace_id": str(WORKSPACE_ID),
                "currency": "USD",
                "budget_state": "limited",
                "window_key": "2026-10",
                "subject_kind": "model",
                "subject_key": "gpt-test-model",
                "pricing_version_id": str(PRICING_VERSION_ID),
                "prices": {
                    "currency": "USD",
                    "input_price": "0.001",
                    "output_price": "0.002",
                    "cached_input_price": None,
                    "tool_unit_price": None,
                },
                "max_cost": "0.200000000000",
                "amount": "0.200000000000",
                "status": "uncertain",
                "run_ref": "run-1",
                "uncertain_reason": "provider timeout",
                "settled_amount": None,
                "overage_amount": None,
                "created_at": "2026-10-16T00:00:00Z",
                "settled_at": None,
                "released_at": None,
                "released_reason": None,
            }
        ],
        "total": 1,
    }
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["params"] = dict(request.url.params)
        if request.url.path.endswith("/usage"):
            return httpx.Response(200, json=usage_payload_page)
        return httpx.Response(200, json=reservations_payload_page)

    client = governance_client(handler)
    usage = asyncio.run(client.costs.usage.list(WORKSPACE_ID))
    assert captured["path"] == f"/api/v1/governance/costs/workspaces/{WORKSPACE_ID}/usage"
    assert usage.usages[0].actual_cost == "0.200000000000"
    assert usage.usages[0].usage.input_tokens == 100

    reservations = asyncio.run(
        client.costs.reservations.list(WORKSPACE_ID, status="uncertain")
    )
    assert captured["params"]["status"] == "uncertain"
    assert reservations.reservations[0].uncertain_reason == "provider timeout"
    assert reservations.reservations[0].max_cost == "0.200000000000"


def test_a_refused_budget_reservation_maps_to_the_rate_limit_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={
                "error": {
                    "code": "budget_exceeded",
                    "message": "The cost budget cannot admit this reservation",
                }
            },
        )

    with pytest.raises(RateLimitError):
        asyncio.run(
            governance_client(handler).costs.budget.upsert(
                WORKSPACE_ID,
                BudgetUpsertRequest(currency="USD", limit_amount="1.5"),
            )
        )
