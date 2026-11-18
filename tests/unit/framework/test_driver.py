import pytest

import opsmesh as framework
from opsmesh import adapters, config
from opsmesh.drivers import Driver
from opsmesh.plugins.activation import Lifespan


class Adapter(adapters.Adapter):
    @classmethod
    def get_name(cls):
        return "test"

    async def _call(self, channel, api, **data):
        raise NotImplementedError


class Channel(adapters.Channel):
    async def send(self, event, message, **kwargs):
        raise NotImplementedError


def new_runtime():
    return Driver(config.Env(), config.Config())


def test_driver_uses_the_core_lifespan_engine():
    driver = new_runtime()

    assert isinstance(driver._lifespan, Lifespan)
    assert type(driver._lifespan).__module__ == "opsmesh.plugins.activation"


def test_framework_has_no_process_global_driver_api():
    from opsmesh import drivers

    for name in ("get_driver", "set_driver", "bind_driver", "is_initialized"):
        assert not hasattr(framework, name)
        assert not hasattr(drivers, name)


def test_adapter_registration_is_owned_by_each_driver():
    first, second = new_runtime(), new_runtime()
    first.register_adapter(Adapter)
    second.register_adapter(Adapter)
    assert (
        first.adapters["test"].driver,
        second.adapters["test"].driver,
    ) == (
        first,
        second,
    )


def test_adapter_lookup_returns_a_copy():
    driver = new_runtime()
    driver.register_adapter(Adapter)
    driver.adapters.clear()
    assert driver.get_adapter(Adapter).driver is driver


def test_channel_lookup_returns_a_snapshot():
    driver = new_runtime()
    driver.register_adapter(Adapter)
    channel = Channel(driver.adapters["test"], "snapshot")
    driver.connect(channel)

    driver.channels.clear()

    assert driver.get_channel("snapshot") is channel
    assert driver.channels.keys() == {"snapshot"}


def test_driver_configuration_is_independent_of_callers():
    settings = config.Config(command_start=frozenset({"!"}))
    driver = Driver(config.Env(environment="staging"), settings)
    assert driver.config == settings
    assert driver.config is not settings
    assert driver.env == "staging"
    assert not hasattr(driver.config, "plugin_configs")


def test_duplicate_registration_preserves_existing_adapter():
    driver = new_runtime()
    adapter = driver.register_adapter(Adapter)
    with pytest.raises(ValueError):
        driver.register_adapter(Adapter)
    assert driver.get_adapter("test") is adapter


def test_adapter_connections_are_read_only_and_isolated():
    class OtherAdapter(Adapter):
        @classmethod
        def get_name(cls):
            return "other"

    driver = new_runtime()
    adapter = driver.register_adapter(Adapter)
    other = driver.register_adapter(OtherAdapter)
    channel = Channel(adapter, "first")
    adapter.connect(channel)
    other.connect(Channel(other, "second"))
    assert list(adapter.channels) == ["first"]
    with pytest.raises(TypeError):
        adapter.channels["injected"] = channel
    adapter.disconnect(channel)
    assert not adapter.channels
    assert list(driver.channels) == ["second"]


def test_disconnect_cannot_remove_another_connection_with_same_id():
    driver = new_runtime()
    adapter = driver.register_adapter(Adapter)
    channel = Channel(adapter, "first")
    adapter.connect(channel)
    with pytest.raises(ValueError):
        driver.disconnect(Channel(adapter, "first"))
    assert driver.get_channel("first") is channel


def test_missing_connection_never_falls_back_to_another():
    driver = new_runtime()
    adapter = driver.register_adapter(Adapter)
    adapter.connect(Channel(adapter, "existing"))
    with pytest.raises(KeyError):
        driver.get_channel("missing")
