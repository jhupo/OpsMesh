import pytest

import opsmesh as framework
from opsmesh import config
from opsmesh.drivers import Driver
from opsmesh.plugins.activation import Lifespan


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


def test_driver_configuration_is_independent_of_callers():
    settings = config.Config(command_start=frozenset({"!"}))
    driver = Driver(config.Env(environment="staging"), settings)
    assert driver.config == settings
    assert driver.config is not settings
    assert driver.env == "staging"
    assert not hasattr(driver.config, "plugin_configs")


def test_driver_does_not_own_channel_connections():
    driver = new_runtime()
    for name in (
        "adapters",
        "channels",
        "_adapters",
        "_channels",
        "register_adapter",
        "get_adapter",
        "get_channel",
        "connect",
        "disconnect",
        "on_connect",
        "on_disconnect",
    ):
        assert not hasattr(driver, name), name


@pytest.mark.anyio
async def test_driver_task_group_is_scoped_to_its_lifespan():
    driver = new_runtime()
    with pytest.raises(RuntimeError, match="Lifespan not started"):
        _ = driver.task_group

    async with driver.lifespan():
        assert driver.started
        assert driver.task_group is driver._lifespan.task_group

    assert not driver.started
    with pytest.raises(RuntimeError, match="Lifespan not started"):
        _ = driver.task_group
