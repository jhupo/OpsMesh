"""Host declarations are usable without embedding a custom API or Worker bootstrap."""

import json
from unittest.mock import Mock

import pytest

from opsmesh.agents.catalog.service import ManagedCatalog
from opsmesh.bootstrap.catalog import load_catalog
from opsmesh.shared.errors import InvalidArgumentError


def declaration():
    return {
        "providers": [
            {
                "id": "primary",
                "name": "Provider",
                "credential_ref": "env:OPSMESH_TEST_MODEL_KEY",
                "models": [{"id": "configured-model", "max_output_tokens": 64}],
            }
        ],
        "agents": [
            {
                "id": "assistant",
                "config": {
                    "type": "agent",
                    "name": "Assistant",
                    "model": {"provider_id": "primary", "model_id": "configured-model"},
                },
            }
        ],
    }


def test_host_catalog_resolves_agent_and_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSMESH_TEST_MODEL_KEY", "credential")
    path = tmp_path / "agents.json"
    path.write_text(json.dumps(declaration()), encoding="utf-8")
    loaded = load_catalog(path)
    reference = loaded.agents.get("assistant").config.model
    provider, model = loaded.providers.select(reference.provider_id, reference.model_id)
    assert provider.credential_ref == "env:OPSMESH_TEST_MODEL_KEY"
    assert model.id == "configured-model"


@pytest.mark.parametrize("defect", ["credential", "model", "duplicate", "disabled_target"])
def test_invalid_catalog_fails_before_process_start(tmp_path, monkeypatch, defect):
    monkeypatch.setenv("OPSMESH_TEST_MODEL_KEY", "credential")
    payload = declaration()
    if defect == "credential":
        payload["providers"][0]["credential_ref"] = "literal-secret"
    elif defect == "model":
        payload["agents"][0]["config"]["model"]["model_id"] = "unregistered"
    elif defect == "duplicate":
        payload["agents"].append(payload["agents"][0])
    else:
        payload["agents"][0]["config"]["tools"] = [{"type": "agent", "agent_id": "disabled"}]
        payload["agents"].append(
            {"id": "disabled", "enabled": False, "config": payload["agents"][0]["config"]}
        )
    path = tmp_path / "agents.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        load_catalog(path)


@pytest.mark.asyncio
async def test_credential_is_resolved_from_current_environment(monkeypatch):
    catalog = ManagedCatalog(Mock(), Mock())
    monkeypatch.setenv("OPSMESH_TEST_MODEL_KEY", "first")
    assert (await catalog.credential("env:OPSMESH_TEST_MODEL_KEY")).get_secret_value() == "first"
    monkeypatch.setenv("OPSMESH_TEST_MODEL_KEY", "rotated")
    assert (await catalog.credential("env:OPSMESH_TEST_MODEL_KEY")).get_secret_value() == "rotated"
    with pytest.raises(InvalidArgumentError):
        await catalog.credential("literal-secret")
