"""A plugin reload cannot change the implementation covered by an approval."""

from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from opsmesh_sdk import PluginContext
from opsmesh_sdk.manifest import PluginManifest

from opsmesh.agents.plugins.registry import PluginRegistry
from opsmesh.bootstrap.workflows import WorkflowTools
from opsmesh.execution.bindings import freeze_bindings
from opsmesh.execution.contracts import PreparedAgent
from opsmesh.execution.tools import PluginTools
from opsmesh.shared.errors import ConflictError
from opsmesh.users.access import CurrentUser
from opsmesh.workflows.contracts import ToolBinding


def registry(path, *, version="1.0.0", config=None, approval=False):
    context = PluginContext(
        manifest=PluginManifest(id="example", name="Example", version=version, kind="tool"),
        source_path=path,
        data_directory=path,
        config={} if config is None else config,
    )

    def execute(value: str) -> str:
        return value

    context.tool("execute", execute, requires_approval=approval)
    result = PluginRegistry()
    result.register(context)
    return result


@pytest.mark.parametrize("change", ["version", "config", "approval", "removed"])
def test_reload_cannot_execute_a_different_admitted_tool(tmp_path, change):
    admitted = registry(tmp_path)
    current = {
        "version": lambda: registry(tmp_path, version="2.0.0"),
        "config": lambda: registry(tmp_path, config={"tools": {"execute": {"target": "new"}}}),
        "approval": lambda: registry(tmp_path, approval=True),
        "removed": PluginRegistry,
    }[change]()
    tools = PluginTools(Mock(), lambda: current, admitted)
    with pytest.raises(ConflictError, match="changed after admission"):
        tools.current("example", "execute")


def test_identical_reload_preserves_admitted_tool_and_hides_config(tmp_path):
    config = {"tools": {"execute": {"token": "private-value"}}}
    admitted = registry(tmp_path, config=config)
    current = registry(tmp_path, config=config)
    assert PluginTools(Mock(), lambda: current, admitted).current("example", "execute") is current
    assert "private-value" not in current.describe_tool("example", "execute").model_dump_json()


def test_serialized_approval_keeps_its_original_plugin_description(tmp_path):
    admitted = registry(tmp_path)
    prepared = PreparedAgent.model_validate(
        {
            "identity": {"run_id": str(uuid4()), "user_id": str(uuid4())},
            "instance_id": str(uuid4()),
            "snapshot": {
                "root_id": "root",
                "definitions": {
                    "root": {
                        "type": "agent",
                        "name": "Root",
                        "tools": [{"type": "tool", "tool_id": "example/tools/execute"}],
                    }
                },
            },
            "tool_descriptions": {
                "example/tools/execute": admitted.describe_tool("example", "execute")
            },
        }
    )
    restored = PreparedAgent.model_validate_json(prepared.model_dump_json())
    current = registry(tmp_path, version="2.0.0")
    bindings = current.agent_bindings(invoke_tool=AsyncMock())
    ledger = Mock()
    with pytest.raises(ConflictError, match="admitted tool"):
        freeze_bindings(bindings, ledger, restored)
    ledger.sessions.assert_not_called()


def test_workflow_approval_tracks_installation_config_outside_tool_overrides(tmp_path):
    actor = CurrentUser(uuid4(), "user@example.com", "User", "active", frozenset({"member"}))
    binding = ToolBinding(kind="plugin", resource_id="example", allowed_operations=["execute"])
    admitted = registry(tmp_path, config={"connection": {"host": "first"}})
    changed = registry(tmp_path, config={"connection": {"host": "second"}})
    adapter = WorkflowTools(Mock(), plugins=lambda: admitted)
    original = adapter.plugin_descriptor(admitted, actor, binding, "execute")
    current = adapter.plugin_descriptor(changed, actor, binding, "execute")
    assert original != current
    assert "first" not in str(original)
