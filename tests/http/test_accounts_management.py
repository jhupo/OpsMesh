"""HTTP and direct service calls share the same account delegation boundary."""

from opsmesh.modules.accounts.api import AccessRole, PermissionGrant


async def test_account_manager_cannot_create_wildcard_role_http(http_platform):
    app, client, headers, principals = http_platform
    await app.services.accounts.create_role(
        AccessRole(name="account-manager", grants=(PermissionGrant(action="accounts:manage"),))
    )
    await app.services.accounts.update_user(principals["alice"], roles=("account-manager",))
    response = await client.post(
        "/api/v1/roles",
        json={"name": "superuser", "grants": [{"action": "*"}]},
        headers=headers["alice"],
    )
    assert response.status_code == 403, response.text
    response = await client.patch(
        f"/api/v1/users/{principals['admin']}", json={"active": False}, headers=headers["alice"]
    )
    assert response.status_code == 403
    response = await client.post(
        "/api/v1/users",
        json={"username": "ordinary", "password": "ordinary-password"},
        headers=headers["alice"],
    )
    assert response.status_code == 201, response.text


async def test_password_change_revokes_current_and_other_http_tokens(http_platform):
    _app, client, headers, principals = http_platform
    sessions_url = f"/api/v1/users/{principals['alice']}/sessions"
    response = await client.get(sessions_url, headers=headers["alice"])
    assert response.status_code == 200 and len(response.json()) == 1
    response = await client.post(
        "/api/v1/auth/password",
        json={"current_password": "incorrect", "new_password": "new-password-123"},
        headers=headers["alice"],
    )
    assert response.status_code == 401
    response = await client.post(
        "/api/v1/auth/password",
        json={"current_password": "alice-password-123", "new_password": "new-password-123"},
        headers=headers["alice"],
    )
    assert response.status_code == 204, response.text
    assert (await client.get("/api/v1/auth/me", headers=headers["alice"])).status_code == 401
    response = await client.post(
        "/api/v1/auth/login", json={"username": "alice", "password": "new-password-123"}
    )
    assert response.status_code == 200
    changed_headers = {"Authorization": f"Bearer {response.json()['token']}"}
    response = await client.post(
        f"/api/v1/users/{principals['alice']}/password/reset",
        json={"new_password": "reset-password-123"},
        headers=headers["admin"],
    )
    assert response.status_code == 204
    assert (await client.get("/api/v1/auth/me", headers=changed_headers)).status_code == 401


async def test_http_role_and_department_revisions_and_reference_errors(http_platform):
    _app, client, headers, _principals = http_platform
    role = {"name": "reader", "grants": [{"action": "accounts:read"}]}
    response = await client.post("/api/v1/roles", json=role, headers=headers["admin"])
    assert response.status_code == 201
    response = await client.put(
        "/api/v1/roles/reader",
        json={"role": {**role, "grants": []}, "expected_revision": 1},
        headers=headers["admin"],
    )
    assert response.status_code == 200 and response.json()["revision"] == 2
    assert (
        await client.delete("/api/v1/roles/reader?expected_revision=1", headers=headers["admin"])
    ).status_code == 409
    assert (
        await client.delete("/api/v1/roles/reader?expected_revision=2", headers=headers["admin"])
    ).status_code == 204
    response = await client.post(
        "/api/v1/departments", json={"name": "Engineering"}, headers=headers["admin"]
    )
    identifier = response.json()["id"]
    response = await client.put(
        f"/api/v1/departments/{identifier}",
        json={"name": "Cycle", "parent_id": identifier, "expected_revision": 1},
        headers=headers["admin"],
    )
    assert response.status_code == 409
    response = await client.delete(
        f"/api/v1/departments/{identifier}?expected_revision=1", headers=headers["admin"]
    )
    assert response.status_code == 204
