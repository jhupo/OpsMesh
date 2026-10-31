import pytest

from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.plugin import OpsMeshPlugin, PluginState
from opsmesh.modules.runtime.internal.extensions import RuntimeExtensionRegistry


def test_cached_extension_checks_plugin_enablement_and_rollback(monkeypatch):
    owner = OpsMeshPlugin(name="plugin", module_name="plugin", state=PluginState.ENABLED)
    monkeypatch.setattr(
        "opsmesh.modules.runtime.internal.extensions.get_current_plugin", lambda: owner
    )
    registry = RuntimeExtensionRegistry()
    registry.register_prompt("prompt", lambda ctx, spec: {"id": "pmpt"})
    callback = registry.prompt("prompt")
    assert callback(None, None) == {"id": "pmpt"}
    owner.state = PluginState.DISABLED
    with pytest.raises(OpsMeshError) as error:
        callback(None, None)
    assert error.value.code == "PLUGIN_DISABLED"
    owner.rollback()
    with pytest.raises(OpsMeshError) as error:
        registry.prompt("prompt")
    assert error.value.code == "EXTENSION_NOT_FOUND"
