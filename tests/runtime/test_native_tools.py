"""Standalone SDK shell, editor and computer tools run real Runner dispatch and approvals."""

import base64
import json

import pytest
from openai.types.responses import (
    ResponseApplyPatchToolCall,
    ResponseComputerToolCall,
    ResponseFunctionShellToolCall,
)

from opsmesh.framework.agents import (
    AgentSpecification,
    ApplyPatchToolBinding,
    ComputerToolBinding,
    FilePatchResult,
    ShellCommandResult,
    ShellExecutionResult,
    ShellToolBinding,
)
from opsmesh.modules.runtime.internal.backends.openai_agents.backend import OpenAIAgentsBackend
from opsmesh.modules.runtime.internal.extensions import RuntimeExtensionRegistry
from tests.runtime.test_openai_backend import ScriptedModel, context, message


@pytest.mark.asyncio
async def test_shell_approval_serialization_and_execution_after_resume():
    seen = []
    registry = RuntimeExtensionRegistry()

    async def execute(ctx, request):
        seen.append(request)
        return ShellExecutionResult(outputs=(ShellCommandResult(stdout="ok", exit_code=0),))

    registry.register_shell("shell@1", execute)
    spec = AgentSpecification(
        id="a", name="A", native_tools=(ShellToolBinding(executor_ref="shell@1"),)
    )
    native = ResponseFunctionShellToolCall(
        id="shell1",
        call_id="shell1",
        type="shell_call",
        status="in_progress",
        action={"commands": ["echo ok"], "timeout_ms": 2000, "max_output_length": 100},
    )
    model = ScriptedModel([[native], [message("done")]])
    backend = OpenAIAgentsBackend(registry, model_resolver=lambda *_: model)
    ctx = context()
    ctx.services["authorize_native_tool"] = lambda binding: None
    pending = await backend.run(spec, "hi", ctx)
    assert not seen and pending.pending[0].arguments["commands"] == ["echo ok"]
    result = await backend.run(
        spec, "ignored", ctx, checkpoint=pending.checkpoint, decisions={pending.pending[0].id: True}
    )
    assert result.output == "done" and seen[0].call_id == "shell1"
    assert "ok" in json.dumps(model.inputs[-1][1]["input"])


@pytest.mark.asyncio
async def test_patch_runs_registered_editor_with_run_context():
    registry = RuntimeExtensionRegistry()
    operations = []
    registry.register_patch(
        "editor@1",
        lambda ctx, op: (
            operations.append((ctx.run_id, op))
            or FilePatchResult(status="completed", output="applied")
        ),
    )
    spec = AgentSpecification(
        id="a",
        name="A",
        native_tools=(ApplyPatchToolBinding(editor_ref="editor@1", requires_approval=False),),
    )
    native = ResponseApplyPatchToolCall(
        id="patch1",
        call_id="patch1",
        type="apply_patch_call",
        status="in_progress",
        operation={"type": "create_file", "path": "hello.txt", "diff": "+hello"},
    )
    model = ScriptedModel([[native], [message("done")]])
    backend = OpenAIAgentsBackend(registry, model_resolver=lambda *_: model)
    ctx = context()
    admissions = []
    ctx.services["authorize_native_tool"] = lambda binding: admissions.append(binding.type)
    assert (await backend.run(spec, "hi", ctx)).output == "done"
    assert operations[0][1].path == "hello.txt" and admissions == ["apply_patch"]


@pytest.mark.asyncio
async def test_computer_actions_safety_and_disposal():
    registry = RuntimeExtensionRegistry()
    actions = []

    class Computer:
        environment = "browser"
        dimensions = (640, 480)
        closed = False

        async def perform(self, action):
            actions.append(action.type)
            if action.type == "screenshot":
                return base64.b64encode(b"image").decode()

        async def close(self):
            self.closed = True

    computer = Computer()
    registry.register_computer("browser@1", lambda ctx: computer)
    registry.register_computer_safety("safe", lambda ctx, data: data["check"]["code"] == "allowed")
    spec = AgentSpecification(
        id="a",
        name="A",
        native_tools=(ComputerToolBinding(computer_ref="browser@1", safety_check_ref="safe"),),
    )
    native = ResponseComputerToolCall(
        id="computer1",
        call_id="computer1",
        type="computer_call",
        status="in_progress",
        action={"type": "click", "x": 10, "y": 20, "button": "left"},
        pending_safety_checks=[{"id": "check1", "code": "allowed", "message": "known workspace"}],
    )
    model = ScriptedModel([[native], [message("done")]])
    backend = OpenAIAgentsBackend(registry, model_resolver=lambda *_: model)
    ctx = context()
    ctx.services["authorize_native_tool"] = lambda binding: None
    assert (await backend.run(spec, "hi", ctx)).output == "done"
    assert actions == ["click", "screenshot"] and computer.closed
