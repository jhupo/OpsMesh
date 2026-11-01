"""Self-service credentials, immutable defaults and owner isolation through the real API."""

import json
import os

import pytest
from pydantic import SecretStr

from opsmesh.framework.agents import AgentSpecification
from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.secrets import FileCredentialCipher
from opsmesh.modules.models.api import ModelProfile


async def credential(client, headers, name="provider", secret="synthetic-private-secret"):
    response = await client.post(
        "/api/v1/me/model-credentials", headers=headers, json={"name": name, "secret": secret}
    )
    assert response.status_code == 201, response.text
    assert secret not in response.text
    return response.json()


async def personal_model(client, headers, reference):
    response = await client.post(
        "/api/v1/me/models",
        headers=headers,
        json={"id": "personal", "model": "offline", "secret_ref": reference},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_credentials_are_encrypted_owner_scoped_versioned_and_revocable(http_platform):
    app, client, headers, principals = http_platform
    original = await credential(client, headers["alice"])
    identifier = original["id"]
    service = app.services.model_credentials
    row = await service.store.get(identifier)
    assert "synthetic-private-secret" not in json.dumps(row.data)
    assert (
        await service.resolve(original["ref"], actor_id=principals["alice"])
        == "synthetic-private-secret"
    )
    key = app.settings.data_directory / "secrets" / "model-credentials.key"
    if os.name != "nt":
        assert key.stat().st_mode & 0o077 == 0
    for username in ("bob", "admin"):
        assert (
            await client.get("/api/v1/me/model-credentials", headers=headers[username])
        ).json() == []
        response = await client.put(
            f"/api/v1/me/model-credentials/{identifier}",
            headers=headers[username],
            json={"secret": "stolen", "expected_revision": 1},
        )
        assert response.status_code == 404
        with pytest.raises(OpsMeshError):
            await service.resolve(original["ref"], actor_id=principals[username])
    response = await client.put(
        f"/api/v1/me/model-credentials/{identifier}",
        headers=headers["alice"],
        json={"secret": "rotated-synthetic-secret", "expected_revision": 1},
    )
    assert response.status_code == 200, response.text
    rotated = response.json()
    assert rotated["version"] == 2 and rotated["ref"] != original["ref"]
    assert (
        await service.resolve(original["ref"], actor_id=principals["alice"])
        == "synthetic-private-secret"
    )
    assert (
        await service.resolve(rotated["ref"], actor_id=principals["alice"])
        == "rotated-synthetic-secret"
    )
    stale = await client.put(
        f"/api/v1/me/model-credentials/{identifier}",
        headers=headers["alice"],
        json={"secret": "stale-secret", "expected_revision": 1},
    )
    assert stale.status_code == 409
    disabled = await client.put(
        f"/api/v1/me/model-credentials/{identifier}/availability",
        headers=headers["alice"],
        json={"enabled": False, "expected_revision": 2},
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["ref"] == rotated["ref"]
    for reference in (original["ref"], rotated["ref"]):
        with pytest.raises(OpsMeshError, match="disabled"):
            await service.resolve(reference, actor_id=principals["alice"])


async def test_personal_models_cannot_import_others_credentials_or_environment_secrets(
    http_platform,
):
    app, client, headers, principals = http_platform
    private = await credential(client, headers["alice"])
    profile = await personal_model(client, headers["alice"], private["ref"])
    assert profile["owner_id"] == principals["alice"]
    assert (await client.get("/api/v1/me/models", headers=headers["bob"])).json() == []
    assert (await client.get("/api/v1/models", headers=headers["admin"])).json() == []
    for username in ("bob", "admin"):
        with pytest.raises(OpsMeshError):
            await app.services.model_management.get(profile["id"], actor_id=principals[username])
        response = await client.post(
            "/api/v1/me/models",
            headers=headers[username],
            json={"id": "forged", "model": "offline", "secret_ref": private["ref"]},
        )
        assert response.status_code == 404, response.text
    for extra in (
        {"secret_ref": "env:PRIVATE_KEY"},
        {"base_url": "http://127.0.0.1:8000/v1"},
        {"base_url": "http://api.openai.com/v1"},
        {"base_url": "https://api.openai.com:8443/v1"},
        {"base_url": "https://api.openai.com:bad/v1"},
    ):
        response = await client.post(
            "/api/v1/me/models",
            headers=headers["alice"],
            json={"id": "rejected", "model": "offline", "secret_ref": private["ref"], **extra},
        )
        assert response.status_code in {400, 403}, response.text
    copied = await client.post(
        "/api/v1/agents",
        headers=headers["bob"],
        json={"id": "copied", "name": "Copied", "model_profile_ref": f"{profile['id']}@1"},
    )
    assert copied.status_code == 201
    rejected = await client.post(
        "/api/v1/agents/copied/publish",
        headers=headers["bob"],
        json={"expected_revision": copied.json()["revision"]},
    )
    assert rejected.status_code == 403, rejected.text


async def test_default_inheritance_pins_versions_and_personal_override_wins(http_platform):
    app, client, headers, principals = http_platform
    shared = await app.services.model_management.create(
        ModelProfile(id="default", model="first"), actor_id=principals["admin"]
    )
    setting = "/api/v1/models/settings/default"
    response = await client.put(
        setting, headers=headers["admin"], json={"reference": "default@1", "expected_revision": 0}
    )
    assert response.status_code == 200, response.text
    assert (await client.get("/api/v1/models/default", headers=headers["admin"])).json()[
        "model"
    ] == "first"
    denied = await client.put(
        setting, headers=headers["alice"], json={"reference": None, "expected_revision": 1}
    )
    assert denied.status_code == 403
    # Publication is local validation and makes no provider request.
    draft = await app.services.agent_management.create(
        AgentSpecification(id="inherited", name="Inherited"), actor_id=principals["admin"]
    )
    published = await app.services.agent_management.publish(
        draft.id, draft.revision, actor_id=principals["admin"]
    )
    assert published.model_profile_ref == "default@1"
    second = await app.services.model_management.update(
        shared.model_copy(update={"model": "second"}), 1, actor_id=principals["admin"]
    )
    assert second.revision == 2
    update = await client.put(
        setting, headers=headers["admin"], json={"reference": "default@2", "expected_revision": 1}
    )
    assert update.status_code == 200
    stale = await client.put(
        setting, headers=headers["admin"], json={"reference": None, "expected_revision": 1}
    )
    assert stale.status_code == 409
    assert (await app.services.agents.get_revision(published.ref)).model_profile_ref == "default@1"
    private = await credential(client, headers["alice"])
    profile = await personal_model(client, headers["alice"], private["ref"])
    own_setting = "/api/v1/me/models/default"
    chosen = await client.put(
        own_setting,
        headers=headers["alice"],
        json={"reference": f"{profile['id']}@1", "expected_revision": 0},
    )
    assert chosen.status_code == 200, chosen.text
    draft = await app.services.agent_management.create(
        AgentSpecification(id="personal-agent", name="Personal"), actor_id=principals["alice"]
    )
    own_agent = await app.services.agent_management.publish(
        draft.id, draft.revision, actor_id=principals["alice"]
    )
    assert own_agent.model_profile_ref == f"{profile['id']}@1"
    cleared = await client.put(
        own_setting, headers=headers["alice"], json={"reference": None, "expected_revision": 1}
    )
    assert cleared.status_code == 200
    assert await app.services.models.default_reference(principals["alice"]) == "default@2"


def test_cipher_never_replaces_a_missing_key_when_decrypting(tmp_path):
    key = tmp_path / "private" / "key"
    cipher = FileCredentialCipher(key)
    encrypted = cipher.encrypt(b"secret")
    assert b"secret" not in encrypted.encode()
    assert cipher.decrypt(encrypted) == b"secret"
    key.unlink()
    with pytest.raises(OpsMeshError, match="key unavailable"):
        cipher.decrypt(encrypted)
    assert not key.exists()


async def test_backup_restores_personal_credential_key_with_database(tmp_path):
    from opsmesh.bootstrap.application import OpsMeshApplication
    from opsmesh.bootstrap.settings import OpsMeshSettings
    from opsmesh.maintenance.backup import create_backup, restore_backup

    settings = OpsMeshSettings(data_directory=tmp_path / "original", worker_enabled=False)
    app = OpsMeshApplication(settings)
    try:
        await app.initialize(admin_username="admin", admin_password="backup-test-password")
        actor = (await app.services.accounts.list_users())[0].id
        created = await app.services.model_credentials.create(
            "test", SecretStr("backup-secret"), actor_id=actor
        )
    finally:
        await app.close()
    manifest = await create_backup(settings, tmp_path / "backup", maintenance=True)
    assert manifest.credential_key is not None
    restored_settings = settings.model_copy(update={"data_directory": tmp_path / "restored"})
    await restore_backup(
        restored_settings,
        tmp_path / "backup",
        expected_manifest_digest=manifest.manifest_digest,
        maintenance=True,
    )
    restored = OpsMeshApplication(restored_settings)
    try:
        await restored.services.database.verify_schema()
        assert (
            await restored.services.model_credentials.resolve(created.ref, actor_id=actor)
            == "backup-secret"
        )
    finally:
        await restored.close()
