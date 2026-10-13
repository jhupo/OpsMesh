from __future__ import annotations

from collections.abc import Generator, Mapping
from datetime import UTC, datetime, timedelta
from uuid import UUID

import fakeredis
import pytest
from fastapi.testclient import TestClient
from limits.storage import MemoryStorage
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.identity.users.models import User
from opsmesh.main import create_app
from opsmesh.orchestration.webhooks.delivery import WebhookDeliveryService
from opsmesh.orchestration.webhooks.http_client import WebhookHTTPResponse
from opsmesh.orchestration.webhooks.models import WebhookSubscription
from opsmesh.orchestration.webhooks.policy import (
    WEBHOOK_REPLAY_COOLDOWN_SECONDS,
    WEBHOOK_REPLAY_WORKSPACE_LIMIT,
)
from opsmesh.orchestration.webhooks.subscriptions import WebhookSubscriptionService
from opsmesh.runtime.temporal.contracts import ExecutionKind
from opsmesh.shared.config import Settings, get_settings
from opsmesh.shared.db.base import Base
from opsmesh.shared.db.session import get_db_session
from opsmesh.shared.redis.dependencies import get_redis_client
from opsmesh.shared.security.rate_limits import FixedWindowRateLimiter
from opsmesh.shared.security.secrets import SecretEncryptionService
from opsmesh.workspaces.management.models import Workspace
from opsmesh.workspaces.members.models import WorkspaceMember
from tests.fixtures.database import product_engine
from tests.fixtures.temporal import durable_executions

TOKEN = "test-token"
SECRET = "test-credential-secret"


def test_webhook_subscription_api_masks_secret_and_disables() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)

    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/webhook-subscriptions",
        headers=_headers(owner.id),
        json={
            "name": "Ops sink",
            "target_url": "https://hooks.example.test/events",
            "event_types": ["task.message.created"],
            "signing_secret": "whsec-super-secret",
        },
    )

    assert created.status_code == 201
    body = created.json()
    assert body["signing_secret"] == "[redacted]"
    assert body["signing_secret_fingerprint"].startswith("sha256:")
    assert "whsec-super-secret" not in created.text

    stored = session.scalar(select(WebhookSubscription))
    assert stored is not None
    decrypted = _secret_service().decrypt_payload(stored.encrypted_signing_secret)
    assert decrypted == {"signing_secret": "whsec-super-secret"}

    listed = client.get(
        f"/api/v1/workspaces/{workspace.id}/webhook-subscriptions",
        headers=_headers(owner.id),
    )
    disabled = client.post(
        f"/api/v1/workspaces/{workspace.id}/webhook-subscriptions/{body['id']}/disable",
        headers=_headers(owner.id),
    )

    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert "whsec-super-secret" not in listed.text
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"
    assert disabled.json()["disabled_at"] is not None


def test_webhook_subscription_update_and_secret_rotate_do_not_leak_secret() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/webhook-subscriptions",
        headers=_headers(owner.id),
        json={
            "name": "Ops sink",
            "target_url": "https://hooks.example.test/events",
            "event_types": ["task.message.created"],
            "signing_secret": "whsec-original-secret",
        },
    )
    subscription_id = created.json()["id"]
    original_fingerprint = created.json()["signing_secret_fingerprint"]

    updated = client.patch(
        f"/api/v1/workspaces/{workspace.id}/webhook-subscriptions/{subscription_id}",
        headers=_headers(owner.id),
        json={
            "name": "Updated sink",
            "target_url": "https://hooks.example.test/updated",
            "event_types": ["*", "task.completed"],
        },
    )
    rotated = client.post(
        (
            f"/api/v1/workspaces/{workspace.id}/webhook-subscriptions/"
            f"{subscription_id}/rotate-signing-secret"
        ),
        headers=_headers(owner.id),
        json={"signing_secret": "whsec-rotated-secret"},
    )

    assert updated.status_code == 200
    assert updated.json()["name"] == "Updated sink"
    assert updated.json()["target_url"] == "https://hooks.example.test/updated"
    assert updated.json()["event_types"] == ["*"]
    assert updated.json()["signing_secret"] == "[redacted]"
    assert "whsec-original-secret" not in updated.text
    assert "whsec-rotated-secret" not in updated.text
    assert rotated.status_code == 200
    assert rotated.json()["signing_secret"] == "[redacted]"
    assert rotated.json()["signing_secret_fingerprint"] != original_fingerprint
    assert "whsec-original-secret" not in rotated.text
    assert "whsec-rotated-secret" not in rotated.text

    stored = session.get(WebhookSubscription, UUID(subscription_id))
    assert stored is not None
    decrypted = _secret_service().decrypt_payload(stored.encrypted_signing_secret)
    assert decrypted == {"signing_secret": "whsec-rotated-secret"}


def test_webhook_event_stages_only_matching_workspace_delivery() -> None:
    session = _session()
    owner, workspace = _seed_workspace(session)
    _, other = _seed_workspace(session, email="other@test.invalid", slug="other-webhooks")
    service = WebhookSubscriptionService(session, _secret_service())
    matching = service.create(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        name="Matching",
        target_url="https://hooks.example.test/matching",
        event_types=["task.message.created"],
        signing_secret="whsec-matching",
    )
    service.create(
        workspace_id=other.id,
        created_by_user_id=owner.id,
        name="Other",
        target_url="https://hooks.example.test/other",
        event_types=["*"],
        signing_secret="whsec-other",
    )
    attempts = WebhookDeliveryService(session).enqueue_event(
        workspace_id=workspace.id,
        event_type="task.message.created",
        event_id="evt-1",
        payload={"message_id": "m-1"},
    )
    session.commit()
    assert [item.subscription_id for item in attempts] == [matching.id]
    requests = durable_executions(session, workspace.id, ExecutionKind.WEBHOOK_DELIVERY)
    assert len(requests) == 1
    assert requests[0].resource_id == attempts[0].id
    assert attempts[0].execution_id == requests[0].execution_id
    assert attempts[0].status == "queued"
    assert durable_executions(session, other.id, ExecutionKind.WEBHOOK_DELIVERY) == []


def test_webhook_delivery_attempt_replay_requeues_idempotently_without_secret_leak() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    subscription = WebhookSubscriptionService(session, _secret_service()).create(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        name="Replay sink",
        target_url="https://hooks.example.test/replay",
        event_types=["*"],
        signing_secret="whsec-replay-secret",
    )
    attempt = WebhookDeliveryService(session).enqueue_event(
        workspace_id=workspace.id,
        event_type="task.failed",
        event_id="evt-replay",
        payload={"task_id": "task-1"},
    )[0]
    attempt.status = "dead_lettered"
    attempt.attempt_count = 1
    attempt.dead_letter_metadata = {"reason": "failed"}
    session.commit()

    url = (
        f"/api/v1/workspaces/{workspace.id}/webhook-subscriptions/{subscription.id}"
        f"/delivery-attempts/{attempt.id}/replay"
    )
    replayed = client.post(url, headers=_headers(owner.id))
    replayed_again = client.post(url, headers=_headers(owner.id))

    assert replayed.status_code == 200
    assert replayed.json()["status"] == "queued"
    assert replayed.json()["dead_letter_metadata"]["replayed_from_status"] == "dead_lettered"
    assert "whsec-replay-secret" not in replayed.text
    assert replayed_again.status_code == 200
    requests = durable_executions(session, workspace.id, ExecutionKind.WEBHOOK_DELIVERY)
    assert len(requests) == 2  # Original delivery plus one explicit replay intent.
    assert requests[-1].resource_id == attempt.id


def test_webhook_delivery_attempt_replay_enforces_cooldown_with_replay_window() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    subscription = WebhookSubscriptionService(session, _secret_service()).create(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        name="Cooldown sink",
        target_url="https://hooks.example.test/cooldown",
        event_types=["*"],
        signing_secret="whsec-cooldown-secret",
    )
    attempt = WebhookDeliveryService(session).enqueue_event(
        workspace_id=workspace.id,
        event_type="task.failed",
        event_id="evt-cooldown",
        payload={"task_id": "task-1"},
    )[0]
    attempt.status = "dead_lettered"
    attempt.dead_letter_metadata = {"replayed_at": datetime.now(UTC).isoformat()}
    session.commit()

    url = (
        f"/api/v1/workspaces/{workspace.id}/webhook-subscriptions/{subscription.id}"
        f"/delivery-attempts/{attempt.id}/replay"
    )
    cooling_down = client.post(url, headers=_headers(owner.id))

    attempt.dead_letter_metadata = {
        "replayed_at": (
            datetime.now(UTC) - timedelta(seconds=WEBHOOK_REPLAY_COOLDOWN_SECONDS + 1)
        ).isoformat()
    }
    session.commit()
    replayed_after_window = client.post(url, headers=_headers(owner.id))

    assert cooling_down.status_code == 429
    assert cooling_down.json()["error"]["details"]["retry_after_seconds"] > 0
    assert replayed_after_window.status_code == 200
    assert replayed_after_window.json()["status"] == "queued"


def test_webhook_delivery_attempt_replay_enforces_workspace_rate_limit() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    subscription = WebhookSubscriptionService(session, _secret_service()).create(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        name="Rate limited sink",
        target_url="https://hooks.example.test/rate-limit",
        event_types=["*"],
        signing_secret="whsec-rate-limit-secret",
    )
    attempts = []
    for index in range(WEBHOOK_REPLAY_WORKSPACE_LIMIT + 1):
        attempt = WebhookDeliveryService(session).enqueue_event(
            workspace_id=workspace.id,
            event_type="task.failed",
            event_id=f"evt-rate-limit-{index}",
            payload={"task_id": f"task-{index}"},
        )[0]
        attempt.status = "dead_lettered"
        attempts.append(attempt)
    session.commit()

    statuses = []
    for attempt in attempts:
        response = client.post(
            (
                f"/api/v1/workspaces/{workspace.id}/webhook-subscriptions/{subscription.id}"
                f"/delivery-attempts/{attempt.id}/replay"
            ),
            headers=_headers(owner.id),
        )
        statuses.append(response.status_code)

    assert statuses[:WEBHOOK_REPLAY_WORKSPACE_LIMIT] == [200] * WEBHOOK_REPLAY_WORKSPACE_LIMIT
    assert statuses[-1] == 429


def test_webhook_delivery_attempt_replay_rejects_disabled_and_cross_workspace() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    _, other_workspace = _seed_workspace(
        session,
        email="other-replay@example.com",
        slug="other-replay",
    )
    subscription_service = WebhookSubscriptionService(session, _secret_service())
    subscription = subscription_service.create(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        name="Disabled replay sink",
        target_url="https://hooks.example.test/disabled-replay",
        event_types=["*"],
        signing_secret="whsec-disabled-replay",
    )
    attempt = WebhookDeliveryService(session).enqueue_event(
        workspace_id=workspace.id,
        event_type="task.failed",
        event_id="evt-disabled-replay",
        payload={"task_id": "task-1"},
    )[0]
    attempt.status = "dead_lettered"
    session.commit()
    subscription_service.disable(workspace_id=workspace.id, subscription_id=subscription.id)

    disabled = client.post(
        (
            f"/api/v1/workspaces/{workspace.id}/webhook-subscriptions/{subscription.id}"
            f"/delivery-attempts/{attempt.id}/replay"
        ),
        headers=_headers(owner.id),
    )
    cross_workspace = WebhookDeliveryService(session).replay_attempt

    assert disabled.status_code == 409
    with pytest.raises(ValueError, match="Webhook delivery attempt not found"):
        cross_workspace(
            workspace_id=other_workspace.id,
            subscription_id=subscription.id,
            delivery_attempt_id=attempt.id,
            rate_limiter=FixedWindowRateLimiter(MemoryStorage()),
        )


def test_webhook_delivery_success_sends_signed_payload_and_redacts_response_headers() -> None:
    session = _session()
    owner, workspace = _seed_workspace(session)
    subscription = WebhookSubscriptionService(session, _secret_service()).create(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        name="Signed sink",
        target_url="https://hooks.example.test/signed",
        event_types=["*"],
        signing_secret="whsec-delivery-secret",
    )
    attempt = WebhookDeliveryService(session).enqueue_event(
        workspace_id=workspace.id,
        event_type="task.message.created",
        event_id="evt-signed",
        payload={
            "message_id": "message-1",
            "signing_secret": "payload-secret",
            "nested": {"ciphertext": "payload-ciphertext"},
        },
    )[0]
    session.commit()
    http_client = _RecordingHTTPClient(
        WebhookHTTPResponse(
            status_code=202,
            body="accepted",
            headers={"Authorization": "Bearer hidden", "X-Request-ID": "req-1"},
        )
    )

    delivered = WebhookDeliveryService(
        session,
        _secret_service(),
        http_client,
    ).deliver(
        workspace_id=workspace.id,
        delivery_attempt_id=attempt.id,
    )

    assert delivered.status == "succeeded"
    assert delivered.attempt_count == 1
    assert delivered.last_status_code == 202
    assert delivered.response_headers == {
        "Authorization": "[redacted]",
        "X-Request-ID": "req-1",
    }
    assert delivered.signature_verified is True
    assert subscription.last_success_at is not None
    assert http_client.calls[0]["url"] == "https://hooks.example.test/signed"
    headers = http_client.calls[0]["headers"]
    assert headers["X-OpsMesh-Event-Id"] == "evt-signed"
    assert headers["X-OpsMesh-Signature"].startswith("sha256=")
    assert b"whsec-delivery-secret" not in http_client.calls[0]["body"]
    assert b"payload-secret" not in http_client.calls[0]["body"]
    assert b"payload-ciphertext" not in http_client.calls[0]["body"]


def test_webhook_delivery_attempt_response_redacts_secret_metadata() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    subscription = WebhookSubscriptionService(session, _secret_service()).create(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        name="Metadata sink",
        target_url="https://hooks.example.test/metadata",
        event_types=["*"],
        signing_secret="whsec-metadata-secret",
    )
    attempt = WebhookDeliveryService(session).enqueue_event(
        workspace_id=workspace.id,
        event_type="task.failed",
        event_id="evt-metadata",
        payload={"task_id": "task-1"},
    )[0]
    attempt.dead_letter_metadata = {
        "signing_secret": "metadata-secret",
        "nested": {"ciphertext": "metadata-ciphertext"},
    }
    session.commit()

    listed = client.get(
        (
            f"/api/v1/workspaces/{workspace.id}/webhook-subscriptions/{subscription.id}"
            "/delivery-attempts"
        ),
        headers=_headers(owner.id),
    )

    assert listed.status_code == 200
    metadata = listed.json()["items"][0]["dead_letter_metadata"]
    assert metadata == {
        "signing_secret": "[redacted]",
        "nested": {"ciphertext": "[redacted]"},
    }
    assert "metadata-secret" not in listed.text
    assert "metadata-ciphertext" not in listed.text


def test_webhook_delivery_dead_letters_after_max_attempts() -> None:
    session = _session()
    owner, workspace = _seed_workspace(session)
    WebhookSubscriptionService(session, _secret_service()).create(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        name="Failing sink",
        target_url="https://hooks.example.test/failing",
        event_types=["*"],
        signing_secret="whsec-failing-secret",
    )
    attempt = WebhookDeliveryService(session).enqueue_event(
        workspace_id=workspace.id,
        event_type="task.progress",
        event_id="evt-failing",
        payload={"step": "draft"},
    )[0]
    attempt.max_attempts = 1
    session.commit()

    delivered = WebhookDeliveryService(
        session,
        _secret_service(),
        _RecordingHTTPClient(WebhookHTTPResponse(status_code=503, body="down", headers={})),
    ).deliver(
        workspace_id=workspace.id,
        delivery_attempt_id=attempt.id,
    )

    assert delivered.status == "dead_lettered"
    assert delivered.dead_lettered_at is not None
    assert delivered.last_status_code == 503
    assert datetime.fromisoformat(delivered.dead_letter_metadata["dead_lettered_at"]) == (
        delivered.dead_lettered_at
    )
    assert delivered.dead_letter_metadata | {"dead_lettered_at": "checked"} == {
        "reason": "Webhook endpoint returned HTTP 503",
        "attempt_count": 1,
        "max_attempts": 1,
        "last_status_code": 503,
        "dead_lettered_at": "checked",
    }


def test_webhook_activity_persists_signed_delivery_result(monkeypatch: pytest.MonkeyPatch) -> None:
    from opsmesh.orchestration.webhooks.activities import WebhookDeliveryActivities
    from opsmesh.runtime.temporal.activities import ActivityDependencies
    from opsmesh.runtime.temporal.contracts import ExecutionRequest

    session = _session()
    owner, workspace = _seed_workspace(session)
    WebhookSubscriptionService(session, _secret_service()).create(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        name="Activity sink",
        target_url="https://hooks.example.test/activity",
        event_types=["*"],
        signing_secret="whsec-act",
    )
    attempt = WebhookDeliveryService(session).enqueue_event(
        workspace_id=workspace.id,
        event_type="task.completed",
        event_id="activity-event",
        payload={"answer": "done"},
    )[0]
    session.commit()
    http = _RecordingHTTPClient(WebhookHTTPResponse(status_code=202, body="ok", headers={}))
    monkeypatch.setattr(
        "opsmesh.orchestration.webhooks.delivery.HttpxWebhookHTTPClient", lambda: http
    )
    activities = WebhookDeliveryActivities(
        ActivityDependencies(
            sessions=sessionmaker(bind=session.get_bind(), expire_on_commit=False),
            settings=_settings(),
        )
    )
    request = ExecutionRequest(
        workspace_id=workspace.id, resource_id=attempt.id, kind=ExecutionKind.WEBHOOK_DELIVERY
    )
    assert activities.deliver(request) == -1
    assert activities.deliver(request) == -1
    session.refresh(attempt)
    assert attempt.status == "succeeded"
    assert len(http.calls) == 1
    assert b"whsec-act" not in http.calls[0]["body"]


class _RecordingHTTPClient:
    def __init__(self, response: WebhookHTTPResponse) -> None:
        self._response = response
        self.calls: list[dict[str, object]] = []

    def post(
        self,
        *,
        url: str,
        body: bytes,
        headers: Mapping[str, str],
        timeout_seconds: int,
    ) -> WebhookHTTPResponse:
        self.calls.append(
            {
                "url": url,
                "body": body,
                "headers": dict(headers),
                "timeout_seconds": timeout_seconds,
            }
        )
        return self._response


def _client() -> tuple[TestClient, Session]:
    engine = product_engine()
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    session = sessions()
    redis = fakeredis.FakeRedis(decode_responses=True)
    app = create_app(_settings(database_url=engine.url.render_as_string(hide_password=False)))
    app.state.rate_limiter = FixedWindowRateLimiter(MemoryStorage())

    def db_session() -> Generator[Session, None, None]:
        with sessions() as request_session:
            yield request_session

    app.dependency_overrides[get_db_session] = db_session
    app.dependency_overrides[get_settings] = lambda: app.state.settings
    app.dependency_overrides[get_redis_client] = lambda: redis
    return TestClient(app), session


def _session() -> Session:
    engine = product_engine()
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _seed_workspace(
    session: Session,
    *,
    email: str = "owner@example.com",
    slug: str = "owner-webhooks",
) -> tuple[User, Workspace]:
    user = User(email=email, display_name=email.split("@")[0])
    workspace = Workspace(owner=user, name=slug.title(), slug=slug, settings={})
    membership = WorkspaceMember(workspace=workspace, user=user, role="owner")
    session.add_all([user, workspace, membership])
    session.commit()
    return user, workspace


def _headers(user_id: object) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {TOKEN}",
        "X-User-ID": str(user_id),
    }


def _settings(database_url: str | None = None) -> Settings:
    return Settings(
        environment="test",
        log_format="text",
        internal_api_token=TOKEN,
        **({"database_url": database_url} if database_url else {}),
        credential_encryption_secret=SECRET,
    )


def _secret_service() -> SecretEncryptionService:
    return SecretEncryptionService(secret=SECRET, key_id="local")
