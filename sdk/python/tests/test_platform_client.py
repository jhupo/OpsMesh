"""Generated platform group contract tests: mail configuration, sends and receipts."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

import httpx

from opsmesh_sdk.api.platform import (
    ReconcileMailRequest,
    SendMailRequest,
    SmtpConfigurationRequest,
    TestMailRequest,
    create_platform_client,
)
from opsmesh_sdk.client import PlatformClient
from opsmesh_sdk.transport.http import HttpTransport

INTENT_ID = UUID("11111111-2222-3333-4444-555555555555")

# The generated DTO name matches pytest's test-class pattern; it is a contract
# model, never a test case.
TestMailRequest.__test__ = False


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


def mail_intent_payload() -> dict[str, Any]:
    return {
        "intent_id": str(INTENT_ID),
        "source": "extension:demo",
        "source_ref": None,
        "source_revision": None,
        "recipient": "user@example.com",
        "subject": "Hello",
        "status": "pending",
        "attempts": 0,
        "outcome_unknown": False,
        "failure_reason": None,
        "created_at": "2026-10-16T00:00:00Z",
        "sent_at": None,
        "reconciled_at": None,
        "reconciliation_note": None,
    }


def test_smtp_update_sends_the_configuration_without_the_previous_password() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "host": "smtp.example.com",
                "port": 587,
                "security": "starttls",
                "username": "mailer",
                "from_email": "ops@example.com",
                "from_name": "OpsMesh",
                "timeout_seconds": 30,
                "password_configured": True,
                "updated_at": "2026-10-16T00:00:00Z",
            },
        )

    view = asyncio.run(
        platform_client(handler).mail.smtp.update(
            SmtpConfigurationRequest(
                host="smtp.example.com",
                port=587,
                security="starttls",
                password="smtp-secret",
                from_email="ops@example.com",
                from_name="OpsMesh",
            )
        )
    )
    assert captured["method"] == "PUT"
    assert captured["body"]["username"] == ""
    assert captured["body"]["timeout_seconds"] == 30
    assert view.password_configured is True


def test_delegated_send_registers_the_intent_with_its_idempotency_key() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=mail_intent_payload())

    intent = asyncio.run(
        platform_client(handler).mail.intents.create(
            SendMailRequest(
                recipient="user@example.com",
                subject="Hello",
                body="Body",
                idempotency_key="mail-key-0001",
                source_ref="invocation-42",
            )
        )
    )
    assert captured["path"] == "/api/v1/platform/mail/intents"
    assert captured["body"]["idempotency_key"] == "mail-key-0001"
    assert captured["body"]["source_ref"] == "invocation-42"
    assert intent.status == "pending"


def test_intent_list_filters_by_status_and_source() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(
            200, json={"intents": [mail_intent_payload()], "total": 1}
        )

    page = asyncio.run(
        platform_client(handler).mail.intents.list(status="needs_attention")
    )
    assert captured["params"]["status"] == "needs_attention"
    assert page.intents[0].intent_id == INTENT_ID


def test_reconcile_confirms_the_outcome_and_omits_an_absent_note() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        payload = mail_intent_payload()
        payload["status"] = "sent"
        return httpx.Response(200, json=payload)

    intent = asyncio.run(
        platform_client(handler).mail.intents.reconcile(
            INTENT_ID, ReconcileMailRequest(outcome="sent")
        )
    )
    assert captured["path"] == f"/api/v1/platform/mail/intents/{INTENT_ID}/reconcile"
    assert captured["body"] == {"outcome": "sent"}
    assert intent.status == "sent"


def test_cancel_returns_the_updated_intent() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        payload = mail_intent_payload()
        payload["status"] = "cancelled"
        return httpx.Response(200, json=payload)

    intent = asyncio.run(platform_client(handler).mail.intents.cancel(INTENT_ID))
    assert intent.status == "cancelled"


def test_test_mail_registers_one_platform_intent() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=mail_intent_payload())

    intent = asyncio.run(
        platform_client(handler).mail.smtp.test(
            TestMailRequest(recipient="user@example.com", idempotency_key="smtp-test-1")
        )
    )
    assert captured["path"] == "/api/v1/platform/mail/smtp/test"
    assert captured["body"] == {
        "recipient": "user@example.com",
        "idempotency_key": "smtp-test-1",
    }
    assert intent.status == "pending"
