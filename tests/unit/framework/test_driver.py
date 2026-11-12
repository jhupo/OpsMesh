import pytest

import opsmesh as framework
from opsmesh import adapters, config
from opsmesh.drivers import Driver
from opsmesh.drivers import base as driver_state


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
    monkeypatch.setattr(driver_state, "_driver", None)


def new_runtime():
    return Driver(config.Env(), config.Config())


def test_uninitialized_driver_access_has_clear_error():
    with pytest.raises(ValueError, match="has not been initialized"):
        framework.get_driver()


def test_public_and_internal_access_return_the_same_driver():
    driver = new_runtime()
    driver_state.set_driver(driver)
    assert framework.get_driver() is driver_state.get_driver() is driver


def test_bound_driver_cannot_be_replaced():
    original = new_runtime()
    driver_state.set_driver(original)
    with pytest.raises(RuntimeError, match="already been initialized"):
        driver_state.set_driver(new_runtime())
    assert driver_state.get_driver() is original


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
    driver_state.set_driver(driver)
    driver.register_adapter(Adapter)
    driver.adapters.clear()
    assert driver.get_adapter(Adapter).driver is driver


def test_channel_lookup_returns_a_snapshot():
    driver = new_runtime()
    driver_state.set_driver(driver)
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
