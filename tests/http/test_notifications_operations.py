"""Notification and operations HTTP APIs use the same scoped services as plugins."""

from opsmesh.dependencies import get_notifications


async def test_notification_http_ownership_template_cas_and_provider_boundary(http_platform):
    app, client, headers, principals = http_platform
    assert get_notifications() is app.services.notification_management
    assert not hasattr(get_notifications(), "observe")
    body = {"id": "inbox", "owner_id": principals["alice"], "event_types": ["runtime.run.failed"]}
    assert (
        await client.post("/api/v1/notifications/subscriptions", json=body, headers=headers["bob"])
    ).status_code == 403
    response = await client.post(
        "/api/v1/notifications/subscriptions", json=body, headers=headers["alice"]
    )
    assert response.status_code == 201, response.text
    response = await client.put(
        "/api/v1/notifications/subscriptions/inbox?expected_revision=1",
        json={**body, "enabled": False},
        headers=headers["alice"],
    )
    assert response.status_code == 200 and response.json()["revision"] == 2
    response = await client.delete(
        "/api/v1/notifications/subscriptions/inbox?expected_revision=1", headers=headers["alice"]
    )
    assert response.status_code == 409
    response = await client.post(
        "/api/v1/notifications/subscriptions",
        json={**body, "id": "bad", "template": "${private_token}"},
        headers=headers["alice"],
    )
    assert response.status_code == 422
    await app.services.notifications.observe(
        event_id="visible-only-to-owner",
        event_type="runtime.run.failed",
        owner_id=principals["alice"],
        resource_id="run",
        status="failed",
    )
    records = (await client.get("/api/v1/notifications", headers=headers["alice"])).json()
    assert len(records) == 1
    assert (await client.get("/api/v1/notifications", headers=headers["bob"])).json() == []
    assert (
        await client.post(
            f"/api/v1/notifications/{records[0]['id']}/read",
            json={"expected_revision": records[0]["revision"]},
            headers=headers["bob"],
        )
    ).status_code == 403


async def test_operational_routes_require_explicit_management_permissions(http_platform):
    _app, client, headers, _principals = http_platform
    for path in (
        "/api/v1/operations/outbox",
        "/api/v1/operations/plugins",
        "/api/v1/operations/plugin-policy",
        "/api/v1/operations/plugin-instances",
    ):
        assert (await client.get(path, headers=headers["alice"])).status_code == 403
        response = await client.get(path, headers=headers["admin"])
        assert response.status_code == 200, response.text
