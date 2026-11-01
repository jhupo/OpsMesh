"""All model registration entry points enforce the same credential boundary."""

import pytest

from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.models.api import ModelProfile, validate_model_profile


async def test_model_configuration_rejection_is_consistent_and_atomic(http_platform):
    app, client, headers, principals = http_platform
    admin = headers["admin"]
    actor = principals["admin"]
    baseline = ModelProfile(
        id="shared",
        model="offline",
        base_url="https://model.example/v1",
        secret_ref="env:TEST_MODEL_KEY",
    )
    await app.services.model_management.create(baseline, actor_id=actor)
    marker = "never-persist-this-value"
    invalid_fields = (
        {"base_url": f"https://{marker}@model.example/v1"},
        {"base_url": f"https://user:{marker}@model.example/v1"},
        {"base_url": f"https://model.example/v1?api_key={marker}"},
        {"base_url": f"https://model.example/v1#{marker}"},
        {"base_url": "file:///tmp/model"},
        {"base_url": "https:///v1"},
        {"secret_ref": marker},
        {"secret_ref": "env:INVALID-NAME"},
        {"settings": {"api_key": marker}},
        {"settings": {"extra_headers": {"Authorization": marker}}},
        {"settings": {"extra_body": [{"X-API-Key": marker}]}},
        {"settings": {"nested": [{"client-secret": marker}]}},
    )
    for fields in invalid_fields:
        update = baseline.model_copy(update=fields)
        create = update.model_copy(update={"id": "rejected"})
        for profile in (create, update):
            with pytest.raises(OpsMeshError) as error:
                validate_model_profile(profile)
            assert error.value.code == "CONFIG_INVALID"
            assert marker not in str(error.value)

        responses = (
            await client.post("/api/v1/models", headers=admin, json=create.model_dump(mode="json")),
            await client.put(
                "/api/v1/models/shared",
                headers=admin,
                json={"profile": update.model_dump(mode="json"), "expected_revision": 1},
            ),
        )
        for response in responses:
            assert response.status_code == 400, response.text
            assert marker not in response.text

        calls = (
            lambda profile=create: app.services.model_management.create(profile, actor_id=actor),
            lambda profile=update: app.services.model_management.update(profile, 1, actor_id=actor),
            lambda profile=create: app.services.models.create(profile),
            lambda profile=update: app.services.models.update(profile, 1),
        )
        for call in calls:
            with pytest.raises(OpsMeshError) as error:
                await call()
            assert error.value.code == "CONFIG_INVALID"
            assert marker not in str(error.value)

        assert await app.services.models.list() == [baseline]
        assert await app.services.models.versions("shared") == (baseline,)


async def test_model_configuration_accepts_versioned_credential_references(http_platform):
    app, client, headers, _ = http_platform
    body = {
        "id": "private-endpoint",
        "model": "offline",
        "api": "responses",
        "base_url": "http://127.0.0.1:9999/v1",
        "secret_ref": "env:PRIVATE_MODEL_KEY",
        "settings": {"temperature": 0.2, "extra_body": {"options": [{"priority": "normal"}]}},
    }
    created = await client.post("/api/v1/models", headers=headers["admin"], json=body)
    assert created.status_code == 201, created.text
    updated = await client.put(
        "/api/v1/models/private-endpoint",
        headers=headers["admin"],
        json={"profile": {**body, "base_url": "https://model.example/v2"}, "expected_revision": 1},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["revision"] == 2
    assert (await app.services.models.get("private-endpoint@1")).base_url == body["base_url"]
    assert (await app.services.models.get("private-endpoint@2")).secret_ref == body["secret_ref"]
