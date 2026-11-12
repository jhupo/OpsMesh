import logging

import pytest
from pydantic import BaseModel

from opsmesh import framework
from opsmesh.framework import adapters, config, drivers, runtime


class Driver(drivers.Driver):
    @property
    def type(self):
        return "test"

    @property
    def logger(self):
        return logging.getLogger(__name__)


class Adapter(adapters.Adapter):
    @classmethod
    def get_name(cls):
        return "test"

    async def _call(self, channel, api, **data):
        raise NotImplementedError


class Channel(adapters.Channel):
    async def send(self, event, message, **kwargs):
        raise NotImplementedError


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch):
    monkeypatch.setattr(runtime, "_driver", None)


def new_driver():
    return Driver(config.Env(), config.Config())


def test_uninitialized_driver_access_has_clear_error():
    with pytest.raises(ValueError, match="has not been initialized"):
        framework.get_driver()


def test_public_and_internal_access_return_the_same_driver():
    driver = new_driver()
    runtime.set_driver(driver)
    assert framework.get_driver() is runtime.get_driver() is driver


def test_plugin_config_uses_the_current_driver_configuration():
    class PluginSettings(BaseModel):
        plugin_limit: int

    driver = Driver(
        config.Env(),
        config.Config(plugin_configs={"example": {"plugin_limit": 17}}),
    )
    runtime.set_driver(driver)
    assert (
        framework.get_plugin_config(
            PluginSettings, namespace="example"
        ).plugin_limit
        == 17
    )


def test_bound_driver_cannot_be_replaced():
    original = new_driver()
    runtime.set_driver(original)
    with pytest.raises(RuntimeError, match="already been initialized"):
        runtime.set_driver(new_driver())
    assert runtime.get_driver() is original


def test_adapter_registration_is_owned_by_each_driver():
    first, second = new_driver(), new_driver()
    first.register_adapter(Adapter)
    second.register_adapter(Adapter)
    assert (first.adapters["test"].driver, second.adapters["test"].driver) == (
        first,
        second,
    )


def test_adapter_lookup_returns_a_copy():
    driver = new_driver()
    runtime.set_driver(driver)
    driver.register_adapter(Adapter)
    framework.get_adapters().clear()
    assert framework.get_adapter(Adapter).driver is driver


def test_channel_lookup_returns_a_snapshot():
    driver = new_driver()
    runtime.set_driver(driver)
    driver.register_adapter(Adapter)
    channel = Channel(driver.adapters["test"], "snapshot")
    driver.connect(channel)

    framework.channels().clear()

    assert framework.channel("snapshot") is channel
    assert driver.channels.keys() == {"snapshot"}


def test_driver_configuration_is_independent_of_callers():
    settings = config.Config(plugin_configs={"example": {"values": [1]}})
    runtime.set_driver(Driver(config.Env(environment="staging"), settings))
    settings.plugin_configs["example"]["values"].append(2)
    assert framework.get_driver().config.plugin_configs["example"] == {
        "values": [1]
    }
    assert framework.get_driver().env == "staging"


def test_plugin_configuration_cannot_mutate_snapshot_or_read_environment(
    monkeypatch,
):
    class PluginSettings(BaseModel):
        values: list[int]

    driver = Driver(
        config.Env(),
        config.Config(
            plugin_configs={"first": {"values": [1]}, "second": {"values": [2]}}
        ),
    )
    runtime.set_driver(driver)
    monkeypatch.setenv("VALUES", "[999]")
    first = framework.get_plugin_config(PluginSettings, namespace="first")
    first.values.append(100)
    assert framework.get_plugin_config(
        PluginSettings, namespace="first"
    ).values == [1]
    assert framework.get_plugin_config(
        PluginSettings, namespace="second"
    ).values == [2]


def test_plugin_configuration_rejects_external_settings_sources():
    from pydantic_settings import BaseSettings

    class PluginSettings(BaseSettings):
        pass

    with pytest.raises(TypeError, match="plain BaseModel"):
        framework.get_plugin_config(PluginSettings, namespace="example")


def test_missing_plugin_settings_apply_defaults_or_require_fields():
    from pydantic import ValidationError

    class OptionalSettings(BaseModel):
        limit: int = 3

    class RequiredSettings(BaseModel):
        limit: int

    runtime.set_driver(new_driver())
    assert (
        framework.get_plugin_config(OptionalSettings, namespace="missing").limit
        == 3
    )
    with pytest.raises(ValidationError):
        framework.get_plugin_config(RequiredSettings, namespace="missing")
