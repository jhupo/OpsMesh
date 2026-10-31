import hashlib
import hmac
import time

from ..http.conftest import http_platform as http_platform
from ..http.conftest import published_agent


async def test_event_trigger_http_to_real_run_and_versioned_management(http_platform):
    app, client, headers, principals = http_platform
    reference = await published_agent(client, headers["admin"])
    body = {
        "id": "incident",
        "principal_id": principals["admin"],
        "agent_revision": reference,
        "source": "monitor",
        "event_type": "incident.opened",
        "filters": {"payload.level": "critical"},
        "input_mapping": {"incident": "payload.id"},
    }
    created = await client.post("/api/v1/triggers", headers=headers["admin"], json=body)
    assert created.status_code == 201, created.text
    event = {
        "id": "notice-1",
        "source": "monitor",
        "type": "incident.opened",
        "payload": {"level": "critical", "id": 10},
    }
    delivered = await client.post("/api/v1/triggers/events", headers=headers["admin"], json=event)
    assert delivered.status_code == 202, delivered.text
    (delivery,) = delivered.json()
    assert delivery["status"] == "submitted" and delivery["run_id"]
    assert (await app.services.runtime.execute(delivery["run_id"])).status == "completed"
    await app.services.triggers.reconcile()
    history = await client.get("/api/v1/triggers/incident/deliveries", headers=headers["admin"])
    assert history.json()[0]["status"] == "completed"
    repeated = await client.post("/api/v1/triggers/events", headers=headers["admin"], json=event)
    assert repeated.json()[0]["run_id"] == delivery["run_id"]
    assert (
        await client.post("/api/v1/triggers/events", headers=headers["alice"], json=event)
    ).status_code == 403
    changed = await client.put(
        "/api/v1/triggers/incident/enabled",
        headers=headers["admin"],
        json={"enabled": False, "expected_revision": 1},
    )
    assert changed.status_code == 200 and changed.json()["revision"] == 2
    assert (
        await client.delete(
            "/api/v1/triggers/incident?expected_revision=1", headers=headers["admin"]
        )
    ).status_code == 409
    assert (
        await client.delete(
            "/api/v1/triggers/incident?expected_revision=2", headers=headers["admin"]
        )
    ).status_code == 204


async def test_signed_webhook_accepts_without_login_and_rejects_tampering(
    http_platform, monkeypatch
):
    _, client, headers, principals = http_platform
    secret = "offline-http-webhook-test-key-at-least-32"
    monkeypatch.setenv("OPSMESH_TEST_WEBHOOK", secret)
    reference = await published_agent(client, headers["admin"])
    created = await client.post(
        "/api/v1/triggers",
        headers=headers["admin"],
        json={
            "id": "webhook",
            "principal_id": principals["admin"],
            "agent_revision": reference,
            "source": "hook",
            "event_type": "notice",
            "webhook_secret_ref": "env:OPSMESH_TEST_WEBHOOK",
        },
    )
    assert created.status_code == 201, created.text
    payload = b'{"text":"notify"}'
    timestamp, delivery_id = str(int(time.time())), "hook-request-1"
    signature = (
        "sha256="
        + hmac.new(
            secret.encode(), f"{timestamp}.{delivery_id}.".encode() + payload, hashlib.sha256
        ).hexdigest()
    )
    signed = {
        "X-OpsMesh-Timestamp": timestamp,
        "X-OpsMesh-Delivery": delivery_id,
        "X-OpsMesh-Signature": signature,
    }
    path = "/api/v1/triggers/webhook/webhook"
    assert (await client.post(path, content=payload)).status_code == 401
    accepted = await client.post(path, content=payload, headers=signed)
    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["run_id"]
    assert secret not in accepted.text
    assert (
        await client.post(path, content=b'{"text":"tampered"}', headers=signed)
    ).status_code == 401
