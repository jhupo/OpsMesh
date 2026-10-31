from pathlib import Path

import pytest

from opsmesh import dependencies
from opsmesh.framework import load_plugin
from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.plugin import manager
from opsmesh.modules.agents.internal.factories import InProcessAgentFactoryRegistry
from opsmesh.modules.capabilities.internal.code_tools import InProcessCodeToolRegistry

DECLARATIONS = """
from pydantic import BaseModel
from opsmesh.dependencies import get_agent_factories, get_code_tools
from opsmesh.framework.agents import AgentSpecification

class Configuration(BaseModel):
    name: str = "test"

def factory(configuration, context):
    return AgentSpecification(id="test", name=configuration.name)

async def echo(arguments):
    return arguments

get_agent_factories().register(
    name="test", version="1", config_schema=Configuration, factory=factory
)
get_code_tools().register("test:echo", echo)
"""


@pytest.mark.parametrize("broken", [False, True])
async def test_platform_declarations_follow_import_rollback_and_disable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, broken: bool
) -> None:
    tools, factories = InProcessCodeToolRegistry(), InProcessAgentFactoryRegistry()
    dependencies.bind_providers({"code_tools": tools, "agent_factories": factories})
    monkeypatch.syspath_prepend(str(tmp_path))
    name = "owned_platform_plugin"
    (tmp_path / f"{name}.py").write_text(
        DECLARATIONS + ('\nraise ValueError("broken")' if broken else ""), encoding="utf-8"
    )
    if broken:
        with pytest.raises(ValueError, match="broken"):
            load_plugin(name)
        with pytest.raises(OpsMeshError) as error:
            tools.resolve("test:echo")
        assert error.value.code == "NOT_FOUND"
        with pytest.raises(OpsMeshError) as error:
            await factories.build("test@1", {}, {})
        assert error.value.code == "NOT_FOUND"
        return
    load_plugin(name)
    callback = tools.resolve("test:echo")
    assert await callback({"text": "hello"}) == {"text": "hello"}
    assert (await factories.build("test@1", {"name": "working"}, {})).name == "working"
    manager.disable(name)
    with pytest.raises(OpsMeshError) as error:
        await callback({})  # Even an already resolved callback rechecks the plugin.
    assert error.value.code == "PLUGIN_DISABLED"
    with pytest.raises(OpsMeshError):
        tools.resolve("test:echo")
    with pytest.raises(OpsMeshError):
        await factories.build("test@1", {}, {})
    manager.enable(name)
    assert await callback({"ok": True}) == {"ok": True}
    tools.freeze()
    with pytest.raises(OpsMeshError) as error:
        tools.register("new", callback)
    assert error.value.code == "REGISTRY_FROZEN"
    tools.register_runtime("resource:generated:run1", callback)
    generated = tools.resolve("resource:generated:run1")
    assert await generated({"ok": 1}) == {"ok": 1}
    tools.unregister_runtime("resource:generated:run1")
    tools.unregister_runtime("resource:generated:run1")
    with pytest.raises(OpsMeshError) as error:
        tools.resolve("resource:generated:run1")
    assert error.value.code == "NOT_FOUND"
    with pytest.raises(OpsMeshError) as error:
        await generated({})
    assert error.value.code == "RESOURCE_RELEASED"
    with pytest.raises(OpsMeshError):
        tools.unregister_runtime("test:echo")
    with pytest.raises(OpsMeshError) as error:
        tools.register_runtime("plugin:new", callback)
    assert error.value.code == "CONFIG_INVALID"
