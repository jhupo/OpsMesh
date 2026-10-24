"""Read isolation, pagination and read-only audit routes over PostgreSQL."""

from fastapi.testclient import TestClient


def test_member_sees_only_own_events_and_cannot_read_another_actor(
    audit_app, audit_readers, audit_facts
):
    _, alice, _ = audit_readers
    with TestClient(audit_app) as client:
        headers = {"X-Session-ID": str(alice.user_id)}
        page = client.get("/api/v1/platform/audit", headers=headers).json()
        assert len(page["events"]) == 2
        assert {event["actor_id"] for event in page["events"]} == {str(alice.user_id)}
        assert (
            client.get(
                f"/api/v1/platform/audit/{audit_facts[-1].event_id}", headers=headers
            ).status_code
            == 404
        )
        own = client.get(f"/api/v1/platform/audit/{audit_facts[2].event_id}", headers=headers)
        assert own.status_code == 200
        assert own.json()["details"]["password"] == "[redacted]"


def test_admin_reads_all_actors_and_cursor_pages_do_not_overlap(
    audit_app, audit_readers, audit_facts
):
    admin, _, _ = audit_readers
    headers = {"X-Session-ID": str(admin.user_id)}
    with TestClient(audit_app) as client:
        first = client.get("/api/v1/platform/audit?limit=3", headers=headers).json()
        second = client.get(
            "/api/v1/platform/audit",
            params={"after_sequence": first["next_after_sequence"]},
            headers=headers,
        ).json()
        assert len(first["events"]) == len(second["events"]) == 3
        assert {item["event_id"] for item in first["events"]}.isdisjoint(
            item["event_id"] for item in second["events"]
        )
        assert (
            client.get(
                f"/api/v1/platform/audit/{audit_facts[-1].event_id}", headers=headers
            ).status_code
            == 200
        )


def test_audit_api_has_no_external_write_or_configuration_scope(audit_app, audit_readers):
    with TestClient(audit_app) as client:
        assert (
            client.post(
                "/api/v1/platform/audit",
                headers={"X-Session-ID": str(audit_readers[0].user_id)},
                json={"action": "forged"},
            ).status_code
            == 405
        )
    operation = audit_app.openapi()["paths"]["/api/v1/platform/audit"]["get"]
    assert "configuration_id" not in {item["name"] for item in operation["parameters"]}
