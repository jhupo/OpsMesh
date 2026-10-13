"""Official native sandbox proxy and approval recovery with real Temporal and PG."""

import asyncio
import json
import os
import selectors
import sys
from dataclasses import replace

import pytest
from agents import Model, ModelResponse
from agents.sandbox.types import ExecResult
from agents.usage import Usage
from openai.types.responses import ResponseCustomToolCall, ResponseFunctionToolCall
from sqlalchemy import select
from temporalio.client import WorkflowFailureError
from temporalio.testing import ActivityEnvironment
from temporalio.worker import Replayer

from opsmesh.agents.execution.providers.openai.workflow import AgentWorkflow
from opsmesh.orchestration.approvals.decisions import ApprovalDecisionService
from opsmesh.orchestration.approvals.models import Approval, PendingToolInvocation
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.runtime.instances.models import WorkspaceRuntime
from opsmesh.runtime.sandboxes.factory import build_sandbox_backends
from opsmesh.runtime.sandboxes.models import SandboxReference
from opsmesh.runtime.sandboxes.service import SandboxService
from opsmesh.runtime.temporal.contracts import WorkflowSignal
from opsmesh.runtime.temporal.converter import sdk_data_converter
from opsmesh.shared.config import Settings
from opsmesh.workspaces.management.models import Workspace
from tests.fixtures.native_agent_worker import OfflineModelProvider, answer, native_agent_worker
from tests.test_agent_native_sandbox_postgres import bound_sandbox

pytest_plugins = ["tests.fixtures.native_agent_execution"]
pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_POSTGRES_URL") or os.getenv("OPSMESH_TEST_TEMPORAL") != "1",
    reason="Real PostgreSQL and Temporal required",
)


@pytest.mark.parametrize("tool_name", ["exec_command", "apply_patch"])
@pytest.mark.parametrize(
    "sandbox_backend",
    [
        "boundary",
        pytest.param(
            "docker",
            marks=[
                pytest.mark.native_sandbox,
                pytest.mark.skipif(
                    sys.platform != "linux" or os.getenv("OPSMESH_TEST_NATIVE_SANDBOX") != "1",
                    reason="Real Linux Docker Worker required",
                ),
            ],
        ),
    ],
)
def test_native_tool_approval_survives_worker_restart_and_replay_without_recreating_compute(
    native_execution,
    tool_name,
    sandbox_backend,
    tmp_path,
):
    state = native_execution
    _, admission, native, client, registry = bound_sandbox(state)
    native.exec.return_value = ExecResult(stdout=b"native evidence", stderr=b"", exit_code=0)
    if sandbox_backend == "docker":
        registry = build_sandbox_backends(
            Settings(environment="test", storage_root=str(tmp_path / "snapshots"))
        )
    with state.sessions() as session:
        workspace = session.get(Workspace, state.request.workspace_id)
        workspace.settings = {
            **workspace.settings,
            "approvals": {
                "rules": [
                    {"id": "native-review", "action": "runtime_command", "decision": "review"}
                ]
            },
        }
        if sandbox_backend == "docker":
            run = session.get(AgentRun, state.request.resource_id)
            session.get(WorkspaceRuntime, run.runtime_id).sandbox_options = {
                "image": os.environ["OPSMESH_TEST_COMPUTE_IMAGE"]
            }
        session.commit()
    arguments = {"cmd": "printf 'native evidence' > evidence.txt"}
    raw_tool = (
        ResponseFunctionToolCall(
            type="function_call",
            name="exec_command",
            call_id="native-original-call",
            arguments=json.dumps(arguments),
        )
        if tool_name == "exec_command"
        else ResponseCustomToolCall(
            type="custom_tool_call",
            name="apply_patch",
            call_id="native-original-call",
            input="*** Begin Patch\n*** Add File: evidence.txt\n+native evidence\n*** End Patch",
        )
    )
    responses = [
        ModelResponse(
            output=[raw_tool],
            usage=Usage(),
            response_id="native-tool",
        ),
        answer("native tool finished"),
    ]
    calls = []
    resources = []

    class NativeModel(Model):
        async def get_response(self, **kwargs):
            calls.append("model")
            return responses.pop(0)

        async def stream_response(self, **kwargs):
            raise AssertionError("Non-streaming validation")
            yield

    async def scenario():
        async with native_agent_worker(
            state, OfflineModelProvider(lambda _: NativeModel()), sandbox_backends=registry
        ) as host:
            async with host.worker():
                handle = await host.environment.client.start_workflow(
                    AgentWorkflow.run,
                    state.request,
                    id=state.request.workflow_id,
                    task_queue=host.task_queue,
                )

                async def paused():
                    while True:
                        with state.sessions() as session:
                            run = session.get(AgentRun, state.request.resource_id)
                            if run.status == "waiting_approval":
                                invocation = session.scalar(select(PendingToolInvocation))
                                assert invocation.tool_kind == "sandbox"
                                assert invocation.tool_call_id == "native-original-call"
                                if sandbox_backend == "boundary":
                                    assert native.exec.await_count == 0
                                    assert native.write.await_count == 0
                                else:
                                    reference = session.scalar(select(SandboxReference))
                                    _, saved = host.sandbox_admission._sandbox._native_state(
                                        host.sandbox_admission._sandbox._reference(reference)
                                    )
                                    resources.append(saved.container_id)
                                return invocation.approval_id
                            assert run.status not in {"failed", "completed", "cancelled"}, run.error
                        await asyncio.sleep(0.05)

                approval_id = await asyncio.wait_for(paused(), 30)
            with state.sessions() as session:
                ApprovalDecisionService(session, admission._secrets).approve(
                    session.get(Approval, approval_id), state.user_id, "Inspected native command"
                )
            await handle.signal("approval", WorkflowSignal(resource_id=approval_id))
            async with host.worker():
                await asyncio.wait_for(handle.result(), 30)
                history = await handle.fetch_history()
            await Replayer(
                workflows=[AgentWorkflow],
                plugins=[host.plugin],
                data_converter=sdk_data_converter,
            ).replay_workflow(history)
            if sandbox_backend == "docker":
                with state.sessions() as session:
                    reference = session.scalar(select(SandboxReference))
                    _, saved = host.sandbox_admission._sandbox._native_state(
                        host.sandbox_admission._sandbox._reference(reference)
                    )
                    assert resources == [saved.container_id]
                    reference_id = reference.id
                environment = ActivityEnvironment()
                environment.info = replace(environment.info, workflow_id=state.request.workflow_id)
                contents = await environment.run(
                    host.sandbox_admission._sandbox.read_file,
                    state.request.workspace_id,
                    reference_id,
                    "evidence.txt",
                    max_bytes=100,
                )
                assert contents.strip() == b"native evidence"
                await environment.run(
                    host.sandbox_admission.checkpoint_native,
                    state.request.workflow_id,
                    saved,
                )

    async def cleanup():
        if sandbox_backend == "docker":
            environment = ActivityEnvironment()
            environment.info = replace(environment.info, workflow_id=state.request.workflow_id)
            with state.sessions() as session:
                references = session.scalars(select(SandboxReference)).all()
                identifiers = [reference.id for reference in references]
            for identifier in identifiers:
                await environment.run(
                    SandboxService(admission._transaction, registry, admission._secrets).delete,
                    state.request.workspace_id,
                    identifier,
                )

    with asyncio.Runner(
        loop_factory=(lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
        if sys.platform == "win32"
        else None,
    ) as runner:
        try:
            runner.run(scenario())
            assert calls == ["model", "model"]
            if sandbox_backend == "boundary":
                if tool_name == "exec_command":
                    native.exec.assert_awaited_once()
                else:
                    native.write.assert_awaited_once()
                client.create.assert_awaited_once()
                client.delete.assert_not_awaited()
            with state.sessions() as session:
                run = session.get(AgentRun, state.request.resource_id)
                assert run.status == "completed" and run.execution_released_at is not None
                reference = session.scalar(select(SandboxReference))
                assert reference.owner_kind == "environment" and reference.status == "stopped"
                invocation = session.scalar(select(PendingToolInvocation))
                assert invocation.status == "completed" and invocation.attempt_count == 1
        finally:
            try:
                runner.run(cleanup())
            finally:
                registry.close()


@pytest.mark.parametrize("native_calls", [1, 2])
def test_native_tool_cancellation_stops_activity_and_retains_only_its_environment(
    native_execution, native_calls
):
    state = native_execution
    _, _, native, client, registry = bound_sandbox(state)

    async def scenario():
        entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        entered_count = cancelled_count = 0

        async def execute(*args, **kwargs):
            nonlocal entered_count, cancelled_count
            entered_count += 1
            if entered_count == native_calls:
                entered.set()
            try:
                await release.wait()
            finally:
                cancelled_count += 1
                if cancelled_count == native_calls:
                    cancelled.set()

        native.exec.side_effect = execute

        class NativeModel(Model):
            async def get_response(self, **kwargs):
                return ModelResponse(
                    output=[
                        ResponseFunctionToolCall(
                            type="function_call",
                            name="exec_command",
                            call_id=f"cancel-native-call-{index}",
                            arguments=json.dumps({"cmd": "sleep 3600"}),
                        )
                        for index in range(native_calls)
                    ],
                    usage=Usage(),
                    response_id="cancel-tool",
                )

            async def stream_response(self, **kwargs):
                raise AssertionError("Non-streaming validation")
                yield

        async with (
            native_agent_worker(
                state, OfflineModelProvider(lambda _: NativeModel()), sandbox_backends=registry
            ) as host,
            host.worker(),
        ):
            handle = await host.environment.client.start_workflow(
                AgentWorkflow.run,
                state.request,
                id=state.request.workflow_id,
                task_queue=host.task_queue,
            )
            await asyncio.wait_for(entered.wait(), 30)
            await handle.cancel()
            try:
                with pytest.raises(WorkflowFailureError):
                    await asyncio.wait_for(handle.result(), 30)
            except TimeoutError:
                history = await handle.fetch_history()
                raise AssertionError(str(history.events[-15:])) from None
            finally:
                release.set()
            assert cancelled.is_set()
            history = await handle.fetch_history()
        await Replayer(
            workflows=[AgentWorkflow],
            plugins=[host.plugin],
            data_converter=sdk_data_converter,
        ).replay_workflow(history)

    with asyncio.Runner(
        loop_factory=(lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
        if sys.platform == "win32"
        else None,
    ) as runner:
        runner.run(scenario())
    client.create.assert_awaited_once()
    client.delete.assert_not_awaited()
    with state.sessions() as session:
        assert session.get(AgentRun, state.request.resource_id).status == "cancelled"
        assert session.scalar(select(SandboxReference)).status == "stopped"
