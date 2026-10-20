"""Private participant conversations and shared file CAS through assembled HTTP routes."""

from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

from opsmesh.identity.users.models import UserProfile
from opsmesh.orchestration.runs.models import Run
from opsmesh.orchestration.runs.scheduling import is_session_head
from tests.execution.test_session_routes import app_client as app_client
from tests.execution.test_session_routes import register_account


def test_invited_users_have_private_fifo_and_shared_versioned_files(app_client, database_sessions):
    participants = []
    for name in ("owner", "a", "b", "c"):
        token = register_account(database_sessions, email=f"{name}-config@example.com")
        headers = {"X-Session-ID": str(token)}
        profile = app_client.get("/api/v1/users/me", headers=headers)
        assert profile.status_code == 200, profile.text
        participants.append((headers, profile.json()["user_id"]))
    owner, *guests = participants
    headers = owner[0]
    with database_sessions.begin() as session:
        session.get(UserProfile, UUID(owner[1])).platform_roles = ["admin"]
    connection = app_client.post(
        "/api/v1/platform/model-connections",
        headers=headers,
        json={
            "name": "model",
            "api_key": "test-only",
            "base_url": "https://example.test/v1",
            "default_model": "test",
            "models": ["test"],
        },
    )
    assert connection.status_code == 201, connection.text
    connection_id = connection.json()["connection_id"]
    for _, participant_id in participants:
        granted = app_client.put(
            f"/api/v1/platform/model-connections/{connection_id}/users/{participant_id}",
            headers=headers,
            json={"models": ["test"], "is_default": True},
        )
        assert granted.status_code == 200, granted.text
    definition = app_client.post(
        "/api/v1/orchestration/definitions",
        headers=headers,
        json={
            "name": "Log analyst",
            "kind": "agent_graph",
            "spec": {
                "entry_agent": "main",
                "agents": {"main": {"name": "Analyst"}},
            },
        },
    )
    assert definition.status_code == 201, definition.text
    published = app_client.post(
        f"/api/v1/orchestration/definitions/{definition.json()['id']}/publish",
        headers=headers,
        json={"expected_revision": 1},
    )
    assert published.status_code == 200, published.text
    configured = app_client.post(
        "/api/v1/orchestration/configurations",
        headers=headers,
        params={"idempotency_key": "create"},
        json={
            "name": "Shared analyst",
            "definition_revision_id": published.json()["id"],
            "specification": {
                "models": {
                    "default": {
                        "connection_id": connection.json()["connection_id"],
                        "name": "test",
                    }
                }
            },
        },
    )
    assert configured.status_code == 201, configured.text
    root = f"/api/v1/orchestration/configurations/{configured.json()['id']}"
    sessions = []
    for guest_headers, guest_id in guests:
        invitation = app_client.post(
            f"{root}/invitations",
            headers=headers,
            json={"user_id": guest_id, "permissions": ["read", "use"], "directory_access": True},
        )
        assert invitation.status_code == 201, invitation.text
        accepted = app_client.post(
            f"/api/v1/orchestration/configurations/invitations/{invitation.json()['id']}/accept",
            headers=guest_headers,
        )
        assert accepted.status_code == 200, accepted.text
        created = app_client.post(f"{root}/sessions", headers=guest_headers)
        assert created.status_code == 201, created.text
        session_id = created.json()["session_id"]
        sessions.append(session_id)
        assert (
            app_client.post(f"{root}/sessions", headers=guest_headers).json()["session_id"]
            == session_id
        )
        listed = app_client.get(f"{root}/sessions", headers=guest_headers)
        assert [item["session_id"] for item in listed.json()] == [session_id]
        written = app_client.post(
            f"/api/v1/orchestration/sessions/{session_id}/history/items",
            headers=guest_headers,
            params={"idempotency_key": "history"},
            json={"items": [{"role": "user", "content": guest_id}]},
        )
        assert written.status_code == 200, written.text
    assert len(set(sessions)) == 3
    assert (
        app_client.get(f"{root}/sessions/{sessions[0]}/history", headers=headers).status_code == 404
    )
    assert (
        app_client.get(f"{root}/sessions/{sessions[0]}/history", headers=guests[1][0]).status_code
        == 404
    )
    cleared = app_client.post(
        f"/api/v1/orchestration/sessions/{sessions[1]}/history/clear",
        headers=guests[1][0],
        params={"idempotency_key": "reset"},
    )
    assert cleared.status_code == 204, cleared.text
    own_history = app_client.get(f"{root}/sessions/{sessions[0]}/history", headers=guests[0][0])
    assert own_history.json()["items"] == [{"role": "user", "content": guests[0][1]}]
    runs = []
    for guest, key in ((guests[0], "a1"), (guests[0], "a2"), (guests[1], "b1"), (guests[2], "c1")):
        submitted = app_client.post(
            f"{root}/runs",
            headers=guest[0],
            params={"idempotency_key": key},
            json={"configuration_revision": 1, "input": "Analyze logs"},
        )
        assert submitted.status_code == 202, submitted.text
        runs.append(UUID(submitted.json()["run_id"]))
    with database_sessions() as session:
        assert [is_session_head(session, session.get(Run, identifier)) for identifier in runs] == [
            True,
            False,
            True,
            True,
        ]
    busy_reset = app_client.post(
        f"/api/v1/orchestration/sessions/{sessions[0]}/history/clear",
        headers=guests[0][0],
        params={"idempotency_key": "reset"},
    )
    assert busy_reset.status_code == 409, busy_reset.text
    shared = app_client.post(
        f"{root}/files",
        headers=guests[0][0],
        params={"idempotency_key": "create"},
        json={"directory_id": configured.json()["root_directory_id"], "name": "index.txt"},
    )
    assert shared.status_code == 201, shared.text
    file_id = shared.json()["file_id"]

    def write(guest):
        return app_client.put(
            f"{root}/files/{file_id}/content",
            headers={**guest[0], "content-type": "text/plain"},
            params={"expected_version": 0, "idempotency_key": "write"},
            content=guest[1].encode(),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(write, guests[:2]))
    assert sorted(result.status_code for result in results) == [200, 409]
    content = app_client.get(f"{root}/files/{file_id}/versions/1/content", headers=guests[2][0])
    assert content.status_code == 200, content.text
    assert content.text in {guest[1] for guest in guests[:2]}
