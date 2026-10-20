"""The renamed audit API retains authorization and has no legacy alias."""

from tests.execution.test_session_routes import app_client as app_client
from tests.platform.test_policies import principals as principals


def test_audit_namespace_and_admin_read_authorization(app_client, principals):
    admin, member = principals
    endpoint = "/api/v1/platform/audit"
    response = app_client.get(endpoint, headers=admin)
    assert response.status_code == 200
    assert "events" in response.json()
    assert app_client.get(endpoint, headers=member).status_code == 403
    assert app_client.get("/api/v1/platform/events", headers=admin).status_code == 404
    # A logged-in administrator cannot impersonate a delegated extension writer.
    assert (
        app_client.post(
            endpoint,
            headers=admin,
            json={
                "action": "forged",
                "target_type": "user",
                "target_id": "other",
                "summary": "forged",
                "idempotency_key": "forged-admin-write",
            },
        ).status_code
        == 401
    )
