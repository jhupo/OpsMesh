"""Real SDK nested approval restores into a fresh independent native sandbox."""

import json
import os
import sys
from datetime import UTC, datetime

import pytest
from agents import set_tracing_disabled
from agents.items import ModelResponse
from agents.models.interface import Model
from agents.usage import Usage
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)

from opsmesh.application.sandbox_bridge import SandboxWorkspaceBridge
from opsmesh.framework.agents import (
    AgentExecutionContext,
    AgentRunCompleted,
    AgentRunInterrupted,
    AgentSpecification,
    AgentToolBinding,
    SandboxConfiguration,
    ToolBinding,
)
from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase
from opsmesh.modules.runtime.api import AgentRunView
from opsmesh.modules.runtime.internal.backends.openai_agents.backend import OpenAIAgentsBackend
from opsmesh.modules.runtime.internal.backends.openai_agents.sandbox_lifecycle import (
    ManagedSandboxSession,
)
from opsmesh.modules.runtime.internal.extensions import RuntimeExtensionRegistry
from opsmesh.modules.workspaces.internal.service import LocalWorkspaceService


def message(text):
    return ResponseOutputMessage(
        id="msg",
        type="message",
        role="assistant",
        status="completed",
        content=[ResponseOutputText(type="output_text", text=text, annotations=[])],
    )


def call(name, arguments, identity):
    return ResponseFunctionToolCall(
        id=identity,
        call_id=identity,
        type="function_call",
        name=name,
        arguments=json.dumps(arguments),
    )


class Scripted(Model):
    def __init__(self, outputs):
        self.outputs, self.inputs = list(outputs), []

    async def get_response(self, *args, **kwargs):
        self.inputs.append((args, kwargs))
        return ModelResponse(
            output=self.outputs.pop(0),
            usage=Usage(requests=1, input_tokens=1, output_tokens=1, total_tokens=2),
            response_id=f"{id(self)}:{len(self.inputs)}",
        )

    async def stream_response(self, *args, **kwargs):
        raise AssertionError("Non-streamed test")
        yield


@pytest.mark.integration
@pytest.mark.skipif(
    sys.platform == "win32" or os.getenv("OPSMESH_TEST_SANDBOX_INTEGRATION") != "1",
    reason="Explicit Unix native sandbox integration",
)
@pytest.mark.asyncio
async def test_isolated_child_approval_restart_preserves_workspace(tmp_path):
    set_tracing_disabled(True)
    child = AgentSpecification(
        id="child",
        name="Child",
        model="offline",
        sandbox=SandboxConfiguration(provider="local", trusted_local=True),
        tools=(ToolBinding(name="approve_write", requires_approval=True),),
    )
    parent = AgentSpecification(
        id="parent",
        name="Parent",
        model="offline",
        agent_tools=(
            AgentToolBinding(agent_ref=child.ref, name="worker", sandbox_policy="isolated"),
        ),
    )
    models = {
        "parent": Scripted(
            [[call("worker", {"input": "work"}, "delegate")], [message("parent done")]]
        ),
        "child": Scripted(
            [
                [
                    call(
                        "exec_command", {"cmd": "printf kept-across-approval > result.txt"}, "write"
                    )
                ],
                [call("approve_write", {}, "approval")],
                [call("exec_command", {"cmd": "cat result.txt"}, "read")],
                [message("child done")],
            ]
        ),
    }
    calls, receipts, contexts = [], [], []
    url = f"sqlite+aiosqlite:///{tmp_path / 'sandbox.db'}"

    async def setup(attempt):
        database = AsyncDatabase(url)
        workspaces = LocalWorkspaceService(database, tmp_path / "data")
        bridge = SandboxWorkspaceBridge(
            workspaces,
            database,
            allow_trusted_local=True,
            session_factory=ManagedSandboxSession,
        )
        await database.initialize()

        async def invoke(name, args, call_id):
            calls.append(call_id)
            return "approved"

        async def resolve(reference):
            return child

        async def observe(value):
            pass

        async def authorize(value):
            pass

        ctx = AgentExecutionContext(
            "parent-run",
            "session",
            "alice",
            invoke,
            resolve,
            services={
                "observe_usage": observe,
                "authorize_model_request": authorize,
                "authorize_workspace": authorize,
            },
        )
        view = AgentRunView(
            id="parent-run",
            revision=1,
            principal_id="alice",
            session_id="session",
            agent=parent,
            status="running",
            attempt_id=attempt,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        ctx.services.update(await bridge.prepare(view, ctx))
        contexts.append(ctx)
        return database, workspaces, bridge, ctx

    database, workspaces, bridge, ctx = await setup("first")
    backend = OpenAIAgentsBackend(
        RuntimeExtensionRegistry(), model_resolver=lambda spec, ctx: models[spec.id]
    )
    try:
        paused = await backend.run(parent, "start", ctx)
        assert isinstance(paused, AgentRunInterrupted)
        receipts.extend(ctx.services["delegated_artifacts"])
        assert (
            await workspaces.read(receipts[-1]["workspace_id"], "result.txt")
            == b"kept-across-approval"
        )
        assert calls == []
    finally:
        await bridge.close()
        await database.close()

    database, workspaces, bridge, ctx = await setup("second")
    backend = OpenAIAgentsBackend(
        RuntimeExtensionRegistry(), model_resolver=lambda spec, ctx: models[spec.id]
    )
    try:
        completed = await backend.run(
            parent,
            "ignored",
            ctx,
            checkpoint=paused.checkpoint,
            decisions={item.id: True for item in paused.pending},
        )
        assert isinstance(completed, AgentRunCompleted)
        assert completed.output == "parent done" and len(calls) == 1
        receipts.extend(ctx.services["delegated_artifacts"])
        assert receipts[0]["workspace_id"] != receipts[1]["workspace_id"]
        assert (
            await workspaces.read(receipts[-1]["workspace_id"], "result.txt")
            == b"kept-across-approval"
        )
        assert "kept-across-approval" in repr(models["child"].inputs[-1])
    finally:
        await bridge.close()
        await database.close()


@pytest.mark.integration
@pytest.mark.skipif(
    sys.platform == "win32" or os.getenv("OPSMESH_TEST_SANDBOX_INTEGRATION") != "1",
    reason="Explicit Unix native sandbox integration",
)
@pytest.mark.asyncio
@pytest.mark.parametrize("fail_child", [False, True])
async def test_concurrent_child_calls_have_separate_sessions_and_outputs(tmp_path, fail_child):
    set_tracing_disabled(True)
    child = AgentSpecification(
        id="child",
        name="Child",
        model="offline",
        sandbox=SandboxConfiguration(provider="local", trusted_local=True),
    )
    parent = AgentSpecification(
        id="parent",
        name="Parent",
        model="offline",
        agent_tools=(
            AgentToolBinding(agent_ref=child.ref, name="worker", sandbox_policy="isolated"),
        ),
    )
    parent_model = Scripted(
        [
            [call("worker", {"input": "one"}, "one"), call("worker", {"input": "two"}, "two")],
            [message("done")],
        ]
    )
    native_sessions, observations = [], []

    def model_resolver(spec, context):
        if spec.id == "parent":
            return parent_model
        session = context.services.get("openai_sandbox_session")
        if session is None:
            return Scripted([])  # Restore graph never executes its model.
        native_sessions.append(session.state.session_id)
        marker = f"child-{len(native_sessions)}"
        result = Scripted(
            [
                [
                    call(
                        "exec_command",
                        {"cmd": f"printf {marker} > result.txt; sleep 0.1; cat result.txt"},
                        "shell",
                    )
                ],
                [message(marker)],
            ]
        )
        if fail_child and marker == "child-2":
            result.outputs.pop()
        return result

    async def invoke(*args):
        raise AssertionError("Only native sandbox tools are expected")

    async def resolve(reference):
        return child

    async def accept(*args):
        return None

    async def observe(value):
        observations.append(value)

    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'parallel.db'}")
    workspaces = LocalWorkspaceService(database, tmp_path / "data")
    bridge = SandboxWorkspaceBridge(
        workspaces,
        database,
        allow_trusted_local=True,
        session_factory=ManagedSandboxSession,
    )
    await database.initialize()
    ctx = AgentExecutionContext(
        "parallel",
        "session",
        "alice",
        invoke,
        resolve,
        services={"observe_usage": observe, "authorize_model_request": accept},
    )
    view = AgentRunView(
        id="parallel",
        revision=1,
        principal_id="alice",
        session_id="session",
        agent=parent,
        status="running",
        attempt_id="attempt",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    ctx.services.update(await bridge.prepare(view, ctx))
    try:
        execution = OpenAIAgentsBackend(
            RuntimeExtensionRegistry(), model_resolver=model_resolver
        ).run(parent, "go", ctx)
        if fail_child:
            with pytest.raises(OpsMeshError):
                await execution
            unknown = [row for row in observations if not row["usage_available"]]
            assert len(unknown) == 1
            assert unknown[0]["run_id"] == "parallel"
            assert unknown[0]["agent_name"].startswith("Child")
            assert not bridge._active
            return
        outcome = await execution
        assert isinstance(outcome, AgentRunCompleted)
        assert len(native_sessions) == len(set(native_sessions)) == 2
        receipts = ctx.services["delegated_artifacts"]
        assert {item["tool_call_id"] for item in receipts} == {"one", "two"}
        assert len({item["workspace_id"] for item in receipts}) == 2
        contents = {await workspaces.read(item["workspace_id"], "result.txt") for item in receipts}
        assert contents == {b"child-1", b"child-2"}
    finally:
        await bridge.close()
        await database.close()
