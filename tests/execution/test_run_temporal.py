"""Real Temporal, native Runner and native PostgreSQL Session integration.

Default cases use deterministic model responses. The separately enabled
real_model case exercises an external Responses provider and actual usage settlement.
"""

import asyncio
import base64
import os
import sys
from contextlib import AsyncExitStack
from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from agents import Model, ModelResponse
from agents.usage import Usage
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)
from sqlalchemy import select
from temporalio.common import RetryPolicy
from temporalio.converter import DataConverter
from temporalio.openai_agents import ModelActivityParameters, OpenAIAgentsPlugin
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker
from temporalio.workflow import ActivityCancellationType

from opsmesh.bootstrap.capabilities import IdentityStorageQuotaLedger
from opsmesh.bootstrap.orchestration import (
    ExecutionApprovalNotifier,
    ExecutionAssets,
    NativeSessionWriter,
)
from opsmesh.bootstrap.routers import ActiveRunActivity, ConfigurationScopeAdmission
from opsmesh.bootstrap.source_notifications import AuthoritativeEvents
from opsmesh.bootstrap.telemetry import configure_process_telemetry
from opsmesh.bootstrap.temporal import ExecutionContextInterceptor
from opsmesh.bootstrap.worker import build_dispatch_worker
from opsmesh.identity.quotas.models import TokenReservation, UserTokenAccount
from opsmesh.identity.quotas.service import set_limits
from opsmesh.orchestration.execution import model_provider
from opsmesh.orchestration.execution.contracts import (
    AssetRef,
    ExecutionLimits,
    ModelConnection,
    RunOutput,
    RunReference,
    ToolConfiguration,
)
from opsmesh.orchestration.runs.agent_workflow import ExecutionRunWorkflow
from opsmesh.orchestration.runs.assets import AssetActivities
from opsmesh.orchestration.runs.model_activities import ExecutionModelProvider
from opsmesh.orchestration.runs.models import OperationReceipt, Run, RunApproval
from opsmesh.orchestration.runs.tool_activities import ToolActivities
from opsmesh.orchestration.runtime.models import Environment, EnvironmentOperationReceipt
from opsmesh.orchestration.sessions.activities import SessionActivities
from opsmesh.orchestration.sessions.history import HistoryGateway
from opsmesh.orchestration.sessions.models import SessionHistoryBatch, SessionRecord
from opsmesh.orchestration.triggers.activities import TriggerActivities
from opsmesh.orchestration.triggers.workflow import SourceEventWorkflow
from opsmesh.platform.approvals import service as approvals
from opsmesh.resources.artifacts.service import read_artifact_content
from opsmesh.resources.files import service as resource_files
from opsmesh.resources.files.access import ResourceReader, ScopeRef
from opsmesh.shared.audit import AuditActor
from opsmesh.temporal.codec import EncryptedPayloadCodec
from opsmesh.temporal.dispatch.models import TemporalOutboxEntry
from opsmesh.temporal.dispatch.outbox import TemporalOutboxPublisher
from tests.conftest import build_test_settings
from tests.execution.test_runs_postgres import (
    Connections,
    harness,  # noqa: F401
)
from tests.execution.test_runtime_postgres import environment as create_environment
from tests.execution.test_runtime_postgres import runtime
from tests.orchestration.test_flows_postgres import flow_facts  # noqa: F401


class ResponseModel(Model):
    calls = 0
    mode = "simple"
    entered = None

    async def get_response(self, *args, **kwargs):
        type(self).calls += 1
        if self.mode == "cancel":
            self.entered.set()
            await asyncio.Event().wait()
        if self.mode == "approval" and type(self).calls == 1:
            return ModelResponse(
                output=[
                    ResponseFunctionToolCall(
                        id="tool-id",
                        call_id="call-1",
                        name="echo",
                        arguments="{}",
                        type="function_call",
                    )
                ],
                usage=Usage(input_tokens=10, output_tokens=5),
                response_id="resp-tool",
            )
        return ModelResponse(
            output=[
                ResponseOutputMessage(
                    id="msg-test",
                    role="assistant",
                    type="message",
                    content=[
                        ResponseOutputText(
                            text="Temporal answer", type="output_text", annotations=[]
                        )
                    ],
                    status="completed",
                )
            ],
            usage=Usage(input_tokens=10, output_tokens=5),
            response_id="resp-test",
        )

    async def stream_response(self, *args, **kwargs):
        raise AssertionError("This test uses the native non-streaming Runner")
        yield


class ResponseProvider:
    def __init__(self, *args, **kwargs):
        pass

    def get_model(self, name):
        return ResponseModel()


class ExternalConnections:
    """Ephemeral acceptance credentials; never part of the frozen Run or history."""

    def resolve(self, session, *, subject, scope, connection_id, model):
        return ModelConnection(
            os.environ["OPSMESH_TEST_MODEL_API_KEY"],
            os.environ["OPSMESH_TEST_MODEL_BASE_URL"],
            8000,
        )


class ToolBackend:
    async def resume(self, payload):
        return object()

    async def execute(self, native, action, arguments):
        return {"stdout": "tool answer", "stderr": "", "exit_code": 0}


class FileBackend:
    def __init__(self, hang_write=False):
        self.files = {}
        self.writes = 0
        self.reads = 0
        self.hang_write = hang_write

    async def resume(self, payload):
        return payload

    def state(self, native):
        return native

    async def execute(self, native, action, arguments):
        if action == "write":
            self.writes += 1
            if self.hang_write:
                await asyncio.Event().wait()
            content = base64.b64decode(arguments["content_base64"], validate=True)
            self.files[arguments["path"]] = content
            return {"bytes_written": len(content)}
        if action == "read":
            self.reads += 1
            return {"content_base64": base64.b64encode(self.files[arguments["path"]]).decode()}
        raise AssertionError("Unexpected file operation")


@pytest.mark.parametrize(
    "mode", ["simple", "approval", "cancel", "files", "files_timeout", "real_model"]
)
def test_native_runner_history_codec_and_replay(
    harness,  # noqa: F811
    database_url,
    monkeypatch,
    mode,
    tmp_path,
):
    if os.environ.get("OPSMESH_TEST_TEMPORAL_LOCAL") != "1":
        pytest.skip("Set OPSMESH_TEST_TEMPORAL_LOCAL=1 to start a real local Temporal server")
    if mode == "real_model":
        if not all(
            os.environ.get(name)
            for name in (
                "OPSMESH_TEST_MODEL_API_KEY",
                "OPSMESH_TEST_MODEL_BASE_URL",
                "OPSMESH_TEST_MODEL_NAME",
            )
        ):
            pytest.skip("External model credentials and model are not configured")
        with harness.runs.sessions() as session, session.begin():
            set_limits(
                session,
                user_id=harness.principal.user_id,
                actor=AuditActor.from_user(harness.principal),
                token_limit=50_000,
            )
        agent = harness.request.agents["main"]
        agent = agent.model_copy(
            update={
                "instructions": "Reply with exactly S7_OK.",
                "model": agent.model.model_copy(
                    update={
                        "name": os.environ["OPSMESH_TEST_MODEL_NAME"],
                        "max_output_tokens": 2048,
                        "reasoning_effort": os.environ.get("OPSMESH_TEST_MODEL_REASONING_EFFORT"),
                    }
                ),
            }
        )
        harness.request = harness.request.model_copy(
            update={"input": "Reply with exactly S7_OK.", "agents": {"main": agent}}
        )
    else:
        monkeypatch.setattr(model_provider, "OpenAIProvider", ResponseProvider)
    ResponseModel.calls = 0
    ResponseModel.mode = mode
    if mode.startswith("files"):
        environment_id = create_environment(harness)
        reader = ResourceReader(harness.principal.user_id)
        with harness.runs.sessions() as session, session.begin():
            directory = resource_files.create_directory(
                session,
                owner_id=reader.user_id,
                path="inputs",
                admission=ConfigurationScopeAdmission(),
                reader=reader,
                scope=ScopeRef(),
            )
            file = resource_files.create_file(
                session,
                owner_id=reader.user_id,
                directory_id=directory.id,
                name="input.txt",
                reader=reader,
            )
            version = resource_files.upload_version(
                session,
                file_id=file.id,
                content=b"frozen input",
                media_type="text/plain",
                reader=reader,
                actor=AuditActor.from_user(harness.principal),
                ledger=IdentityStorageQuotaLedger(),
                storage_root=tmp_path,
                idempotency_key="input-one",
            )
            resource_files.set_runtime_policy(
                session,
                file_id=file.id,
                frozen_input_versions=[version.version],
                relative_paths=["input.txt"],
                output_declarations=[],
                use_in_execution=True,
                reader=reader,
                actor=AuditActor.from_user(harness.principal),
            )
        harness.request = harness.request.model_copy(
            update={
                "environment_id": environment_id,
                "assets": [
                    AssetRef(
                        ref=f"file:{file.id}:{version.version}",
                        digest=version.digest,
                        destination="input.txt",
                    )
                ],
                "outputs": [RunOutput(path="input.txt", name="result.txt")],
            }
        )
        if mode == "files_timeout":
            harness.request = harness.request.model_copy(
                update={"limits": ExecutionLimits(execution_timeout_seconds=1)}
            )
    if mode == "approval":
        with harness.runs.sessions() as session, session.begin():
            environment = Environment(
                scope_id=harness.principal.user_id,
                principal_id=harness.principal.user_id,
                owner_ref=f"user:{harness.principal.user_id}",
                status="ready",
                configuration={},
                encrypted_state=harness.runs.cipher.encrypt_payload({}).ciphertext,
            )
            session.add(environment)
            session.flush()
            environment_id = environment.id
        agent = harness.request.agents["main"].model_copy(
            update={
                "tools": [
                    ToolConfiguration(
                        name="echo",
                        description="Echo",
                        input_schema={
                            "type": "object",
                            "properties": {},
                            "additionalProperties": False,
                        },
                        command=["echo"],
                        approval_required=True,
                    )
                ]
            }
        )
        harness.request = harness.request.model_copy(
            update={"environment_id": environment_id, "agents": {"main": agent}}
        )
    record = harness.submit()

    async def verify():
        ResponseModel.entered = asyncio.Event()
        history_gateway = HistoryGateway(harness.runs.sessions)
        session_activities = SessionActivities(
            harness.runs.sessions,
            history_gateway,
            harness.runs.admission,
            ActiveRunActivity(),
            NativeSessionWriter(),
        )
        files = FileBackend(hang_write=mode == "files_timeout")
        assets = AssetActivities(
            harness.runs,
            runtime(harness, files),
            ExecutionAssets(tmp_path, ConfigurationScopeAdmission()),
        )
        provider = ExecutionModelProvider(
            harness.runs.sessions,
            harness.runs.bindings,
            harness.runs.admission,
            ExternalConnections() if mode == "real_model" else Connections(),
            harness.runs.cipher,
        )
        plugin = OpenAIAgentsPlugin(
            model_provider=provider,
            model_params=ModelActivityParameters(
                start_to_close_timeout=timedelta(seconds=120 if mode == "real_model" else 20),
                heartbeat_timeout=timedelta(seconds=10),
                retry_policy=RetryPolicy(maximum_attempts=1),
                cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
            ),
        )
        converter = replace(
            DataConverter(), payload_codec=EncryptedPayloadCodec(harness.runs.cipher)
        )
        try:
            async with await WorkflowEnvironment.start_local(
                plugins=[plugin], data_converter=converter
            ) as env:
                queue = f"s7-test-{uuid4()}"
                async with Worker(
                    env.client,
                    task_queue=queue,
                    workflows=[ExecutionRunWorkflow],
                    interceptors=[ExecutionContextInterceptor()],
                    activities=[
                        harness.runs.prepare,
                        harness.runs.finish,
                        harness.runs.checkpoint,
                        harness.runs.apply_approval,
                        session_activities.operate,
                        ToolActivities(harness.runs, ToolBackend()).invoke,
                        assets.transfer,
                        assets.collect,
                    ],
                ):
                    handle = await env.client.start_workflow(
                        ExecutionRunWorkflow.run,
                        RunReference(
                            execution_id=record.id, run_id=record.id, workflow_id=f"run:{record.id}"
                        ),
                        id=f"run:{record.id}",
                        task_queue=queue,
                    )
                    if mode == "cancel":
                        await asyncio.wait_for(ResponseModel.entered.wait(), timeout=15)
                        await handle.signal("cancel", str(uuid4()))
                    elif mode == "approval":

                        async def wait_approval():
                            while True:
                                with harness.runs.sessions() as session:
                                    checkpoint = session.scalar(
                                        select(RunApproval).where(RunApproval.run_id == record.id)
                                    )
                                    if checkpoint is not None:
                                        return checkpoint.approval_id
                                await asyncio.sleep(0.05)

                        approval_id = await asyncio.wait_for(wait_approval(), timeout=15)
                        with harness.runs.sessions() as session, session.begin():
                            approvals.decide(
                                session,
                                approval_id=approval_id,
                                decision="approved",
                                decided_by=harness.principal,
                                reason=None,
                                idempotency_key="approval-one",
                                notifier=ExecutionApprovalNotifier(),
                            )
                        await handle.signal("approval", args=[str(uuid4()), approval_id])
                    await asyncio.wait_for(
                        handle.result(), timeout=150 if mode == "real_model" else 45
                    )
                    workflow_history = await handle.fetch_history()
                    with harness.runs.sessions() as session:
                        result = session.get(Run, record.id)
                        assert result.status == (
                            "needs_attention"
                            if mode in {"cancel", "files_timeout"}
                            else "succeeded"
                        ), result.error_code
                        if mode not in {"cancel", "files_timeout"}:
                            assert harness.runs.cipher.decrypt_payload(result.result_ciphertext)[
                                "output"
                            ] == ("S7_OK" if mode == "real_model" else "Temporal answer")
                        if mode == "real_model":
                            receipt = session.scalar(
                                select(OperationReceipt).where(OperationReceipt.run_id == record.id)
                            )
                            assert receipt.status == "completed" and receipt.accounted
                            token_hold = session.scalar(select(TokenReservation))
                            assert token_hold.status == "settled"
                            assert receipt.usage == result.usage
                            account = session.get(UserTokenAccount, harness.principal.user_id)
                            assert account.reserved_tokens == 0 and account.used_tokens > 0
                            native_usage = harness.runs.cipher.decrypt_payload(
                                receipt.result_ciphertext
                            )["result"]["usage"]
                            assert account.used_tokens == (
                                native_usage["input_tokens"] + native_usage["output_tokens"]
                            )
                            settled_tokens = account.used_tokens
                        if mode == "files":
                            artifact_ids = harness.runs.cipher.decrypt_payload(
                                result.result_ciphertext
                            )["artifacts"]
                            assert len(artifact_ids) == 1
                            _, content = read_artifact_content(
                                session,
                                artifact_id=UUID(artifact_ids[0]),
                                reader=ResourceReader(harness.principal.user_id),
                                storage_root=tmp_path,
                            )
                            assert content == b"frozen input"
                            assert (files.writes, files.reads) == (1, 1)
                        if mode == "files_timeout":
                            receipt = session.get(
                                EnvironmentOperationReceipt,
                                (harness.principal.user_id, f"asset:{record.id}:0"),
                            )
                            assert receipt.status == "uncertain"
                            assert (
                                session.get(Environment, environment_id).status == "needs_attention"
                            )
                            assert (files.writes, files.reads) == (1, 0)
                    native_history = await history_gateway.items(
                        str(harness.request.session_id), limit=None
                    )
                    expected_items = {"approval": 4, "cancel": 0, "files_timeout": 0}.get(mode, 2)
                    expected_calls = {"approval": 2, "files_timeout": 0}.get(mode, 1)
                    assert len(native_history) == expected_items
                    if mode != "real_model":
                        assert ResponseModel.calls == expected_calls
                    if mode == "files":
                        assert (files.writes, files.reads) == (1, 1)
                    raw = b"".join(event.SerializeToString() for event in workflow_history.events)
                    assert b"Temporal answer" not in raw and b"local-test-key" not in raw
                    if mode == "real_model":
                        assert os.environ["OPSMESH_TEST_MODEL_API_KEY"].encode() not in raw
                    await Replayer(
                        workflows=[ExecutionRunWorkflow],
                        plugins=[plugin],
                        data_converter=env.client.data_converter,
                    ).replay_workflow(workflow_history)
                    if mode == "real_model":
                        with harness.runs.sessions() as session:
                            assert len(session.scalars(select(TokenReservation)).all()) == 1
                            assert len(session.scalars(select(OperationReceipt)).all()) == 1
                            account = session.get(UserTokenAccount, harness.principal.user_id)
                            assert account.used_tokens == settled_tokens
                            assert account.reserved_tokens == 0
                            print(
                                f"Real model settled {settled_tokens} tokens; "
                                "replay kept one receipt"
                            )
                    else:
                        assert ResponseModel.calls == expected_calls
                    if mode == "files_timeout":
                        assert (files.writes, files.reads) == (1, 0)
        finally:
            pass

    with asyncio.Runner(
        loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None
    ) as runner:
        runner.run(verify())


@pytest.mark.skipif(
    os.environ.get("OPSMESH_TEST_TEMPORAL_LOCAL") != "1", reason="Real Temporal opt-in"
)
def test_same_session_models_execute_fifo_and_histories_publish_once(
    request,
    monkeypatch,
    async_database_sessions,
    flow_facts,  # noqa: F811
):
    shared = request.getfixturevalue("harness")
    records = [shared.submit("fifo-A"), shared.submit("fifo-B")]

    async def verify():
        entered = 0
        active = 0
        model_inputs = []

        class ConcurrentModel(ResponseModel):
            mode = "simple"
            calls = 0

            async def get_response(self, *args, **kwargs):
                nonlocal entered, active
                entered += 1
                active += 1
                model_inputs.append(kwargs["input"])
                try:
                    assert active == 1
                    await asyncio.sleep(0.1)
                    return await super().get_response(*args, **kwargs)
                finally:
                    active -= 1

        class ConcurrentProvider(ResponseProvider):
            def get_model(self, name):
                return ConcurrentModel()

        monkeypatch.setattr(model_provider, "OpenAIProvider", ConcurrentProvider)
        gateway = HistoryGateway(shared.runs.sessions)
        session_activities = SessionActivities(
            shared.runs.sessions,
            gateway,
            shared.runs.admission,
            ActiveRunActivity(),
            NativeSessionWriter(),
        )
        provider = ExecutionModelProvider(
            shared.runs.sessions,
            shared.runs.bindings,
            shared.runs.admission,
            Connections(),
            shared.runs.cipher,
        )
        plugin = OpenAIAgentsPlugin(
            model_provider=provider,
            model_params=ModelActivityParameters(
                start_to_close_timeout=timedelta(seconds=30),
                heartbeat_timeout=timedelta(seconds=10),
                retry_policy=RetryPolicy(maximum_attempts=1),
            ),
        )
        converter = replace(
            DataConverter(), payload_codec=EncryptedPayloadCodec(shared.runs.cipher)
        )
        async with await WorkflowEnvironment.start_local(  # noqa: SIM117
            plugins=[plugin], data_converter=converter
        ) as env:
            flow_runtime, _, _ = flow_facts
            queue = f"fifo-session-{uuid4()}"
            triggers = TriggerActivities(
                flow_runtime,
                AuthoritativeEvents(flow_runtime.cipher, flow_runtime.scopes),
                env.client,
                queue,
            )
            async with Worker(
                env.client,
                task_queue=queue,
                workflows=[ExecutionRunWorkflow, SourceEventWorkflow],
                activities=[
                    triggers.consume,
                    shared.runs.prepare,
                    shared.runs.finish,
                    session_activities.operate,
                ],
            ) as worker:
                publisher = TemporalOutboxPublisher(
                    async_database_sessions,
                    env.client,
                    task_queue=worker.task_queue,
                    concurrency=2,
                    cipher=shared.runs.cipher,
                )
                assert await publisher.publish_pending() >= 2
                with shared.runs.sessions() as session:
                    intents = session.scalars(
                        select(TemporalOutboxEntry).where(
                            TemporalOutboxEntry.workflow_id.in_(
                                [f"run:{record.id}" for record in records]
                            ),
                            TemporalOutboxEntry.operation == "start",
                        )
                    ).all()
                    assert len(intents) == 2
                    assert all(intent.published_at is not None for intent in intents)
                handles = [env.client.get_workflow_handle(f"run:{record.id}") for record in records]
                await asyncio.wait_for(handles[0].result(), 30)
                # Finishing the first Run atomically stages the second Run's wake signal.
                assert await publisher.publish_pending() >= 1
                await asyncio.wait_for(handles[1].result(), 30)
                assert entered == ConcurrentModel.calls == 2
                assert len(model_inputs[0]) == 1 and len(model_inputs[1]) == 3
                assert model_inputs[1][1]["content"][0]["text"] == "Temporal answer"
                with shared.runs.sessions() as session:
                    assert {session.get(Run, record.id).status for record in records} == {
                        "succeeded"
                    }
                    assert (
                        session.get(SessionRecord, shared.request.session_id).history_revision == 2
                    )
                    batches = list(session.scalars(select(SessionHistoryBatch)))
                    assert len(batches) == 2
                    assert {batch.committed_revision for batch in batches} == {1, 2}
                assert len(await gateway.items(str(shared.request.session_id), limit=None)) == 4
                for handle in handles:
                    await Replayer(
                        workflows=[ExecutionRunWorkflow],
                        plugins=[plugin],
                        data_converter=env.client.data_converter,
                    ).replay_workflow(await handle.fetch_history())
                assert entered == ConcurrentModel.calls == 2
                assert len(await gateway.items(str(shared.request.session_id), limit=None)) == 4

    with asyncio.Runner(
        loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None
    ) as runner:
        runner.run(verify())


@pytest.mark.skipif(
    os.environ.get("OPSMESH_TEST_TEMPORAL_LOCAL") != "1", reason="Real Temporal opt-in"
)
def test_actual_worker_assembly_registers_execution(database_url, database_sessions):
    async def verify():
        async with await WorkflowEnvironment.start_local() as env, AsyncExitStack() as resources:
            settings = build_test_settings(database_url).model_copy(
                update={
                    "temporal_address": env.client.service_client.config.target_host,
                    "temporal_task_queue": f"s7-assembly-{uuid4()}",
                }
            )
            configure_process_telemetry(settings)
            assembled = await build_dispatch_worker(settings, resources)
            async with assembled.worker:
                configuration = assembled.worker.config()
                assert ExecutionRunWorkflow in configuration["workflows"]
                names = {
                    getattr(item, "__temporal_activity_definition").name
                    for item in configuration["activities"]
                }
                assert {
                    'orchestration.prepare',
                    'orchestration.assets',
                    'orchestration.outputs',
                    'orchestration.runtime',
                    'orchestration.mcp.lifecycle',
                    'orchestration.session',
                } <= names

    with asyncio.Runner(
        loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None
    ) as runner:
        runner.run(verify())
