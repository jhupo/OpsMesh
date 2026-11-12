"""Verify plugin-owned configuration independent of framework runtime."""

from contextvars import ContextVar
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ValidationError

from opsmesh import get_plugin_config
from opsmesh.plugins import config as plugin_config


class Settings(BaseModel):
    """Required values used to test copy and namespace boundaries."""

    values: list[int]


@pytest.fixture
def owner(monkeypatch):
    plugin = SimpleNamespace(
        config_namespace="example", config_values={"values": [1]}
    )
    monkeypatch.setattr(
        plugin_config,
        "_current_plugin",
        ContextVar("test_owner", default=plugin),
    )
    return plugin


def test_configuration_uses_plugin_values_without_driver(owner, monkeypatch):
    monkeypatch.setenv("VALUES", "[999]")
    settings = get_plugin_config(Settings, namespace="example")
    settings.values.append(2)
    assert owner.config_values == {"values": [1]}
    assert get_plugin_config(Settings, namespace="example").values == [1]


def test_plugin_cannot_read_another_namespace(owner):
    with pytest.raises(ValueError, match="another namespace"):
        get_plugin_config(Settings, namespace="other")


def test_defaults_and_required_values_belong_to_plugin_model(owner):
    class Defaults(BaseModel):
        limit: int = 3

    owner.config_values = None
    assert get_plugin_config(Defaults, namespace="example").limit == 3
    with pytest.raises(ValidationError):
        get_plugin_config(Settings, namespace="example")


def test_plugin_configuration_rejects_external_settings_sources():
    from pydantic_settings import BaseSettings

    class ExternalSettings(BaseSettings):
        pass

    with pytest.raises(TypeError, match="plain BaseModel"):
        get_plugin_config(ExternalSettings, namespace="example")
