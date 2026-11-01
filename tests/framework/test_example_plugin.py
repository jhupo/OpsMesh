import logging
from pathlib import Path

import pytest

from opsmesh.bootstrap.application import OpsMeshApplication
from opsmesh.bootstrap.settings import OpsMeshSettings
from opsmesh.framework import load_plugin
from opsmesh.framework.adapters import MessageEvent, OpsMeshMessage
from opsmesh.framework.agents import AgentExecutionContext, bind_execution_context
from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.events import DispatchContext
from opsmesh.framework.message import handle_event


async def test_example_plugin_handler_yield_factory_tool_and_hook(tmp_path, monkeypatch, caplog):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[2] / "examples" / "custom_plugin"))
    application = OpsMeshApplication(OpsMeshSettings(data_directory=tmp_path, worker_enabled=False))
    caplog.set_level(logging.INFO, logger="opsmesh_example_plugin")
    try:
        plugin = load_plugin("opsmesh_example_plugin")
        assert plugin.metadata.name == "Example assistant"
        context = DispatchContext(
            MessageEvent(
                id="event-1", message=OpsMeshMessage("/hello Ada"), user_id="one", session_id="chat"
            )
        )
        result = await handle_event(context, raise_on_error=True)
        assert result.matched == 1
        assert context.values["example_reply"] == "Hello [event-1], Ada!"
        assert "example resource acquired: event-1" in caplog.text
        assert "example resource released: event-1" in caplog.text
        specification = await application.services.agent_factories.build(
            "example.assistant@1", {"agent_id": "a", "model_profile_ref": "model@1"}, {}
        )
        assert specification.hooks[0].ref == "example.observe@1"

        async def invoke(name, arguments, call_id):
            raise AssertionError("This example performs no remote tools")

        execution = AgentExecutionContext("run-1", "session-1", "user-1", invoke)
        callback = application.services.code_tools.resolve("example:uppercase@1")
        with pytest.raises(OpsMeshError) as error:
            await callback({"text": "hello"})
        assert error.value.code == "CONTEXT_UNAVAILABLE"
        with bind_execution_context(execution):
            assert await callback({"text": "hello"}) == {"text": "HELLO", "run_id": "run-1"}
            hook = application.services.extensions.hook("example.observe@1")
            await hook(execution, "agent.start", {})
        assert execution.extensions["example_hook_events"] == ["agent.start"]
    finally:
        await application.close()
