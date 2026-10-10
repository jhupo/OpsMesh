import asyncio
from contextlib import suppress
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from opsmesh.agents.execution.contracts import AgentRuntimeContext, AgentRuntimeExecutionBinding
from opsmesh.agents.execution.tools.scoped import ScopedToolExecutor
from opsmesh.capabilities.mcp.models import McpServer, McpToolAllowlist, McpToolCallLog
from opsmesh.orchestration.approvals.models import Approval
from opsmesh.orchestration.approvals.pending_tools import (
    PendingToolInvocationRequest,
    PendingToolInvocationService,
)
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.runs.state import RunStatus
from opsmesh.orchestration.tasks.models import TaskStep
from opsmesh.runtime.instances.models import WorkspaceRuntime
from opsmesh.runtime.self_hosted.dispatch.jobs import SelfHostedJobFinalizer
from opsmesh.runtime.self_hosted.models import SelfHostedMcpJob
from opsmesh.runtime.self_hosted.worker.events import SelfHostedEventRecorder
from opsmesh.shared.concurrency import BlockingIO
from opsmesh.shared.config import Settings
from opsmesh.shared.db.operations import DatabaseOperations
from opsmesh.shared.security.secrets import SecretEncryptionService
from opsmesh.workspaces.management.models import Workspace
from tests.test_agent_runtime_tools import _mcp_definition, _set_mcp_snapshot
from tests.test_worker_runner import _seed_run, _session_factory


def test_approved_runtime_rpc_waits_and_recovers_without_reexecution():
    factory = _session_factory()
    workspace_id, run_id, user_id = _seed_run(factory, slug="sdk-rpc")
    settings = Settings(environment="test")
    secrets = SecretEncryptionService(
        secret=settings.credential_encryption_secret, key_id=settings.credential_encryption_key_id
    )
    with factory() as session:
        run = session.get(AgentRun, run_id)
        workspace = session.get(Workspace, workspace_id)
        runtime = WorkspaceRuntime(
            workspace_id=workspace_id,
            name="RPC",
            runtime_provider="self_hosted",
            runtime_type="self_hosted",
            status="active",
            connection_status="online",
        )
        server = McpServer(
            workspace_id=workspace_id,
            name="RPC",
            server_type="stdio",
            connection={"command": "offline"},
        )
        session.add_all([runtime, server])
        session.flush()
        allow = McpToolAllowlist(
            workspace_id=workspace_id,
            mcp_server_id=server.id,
            tool_name="inspect",
            requires_approval=True,
            policy={"max_calls_per_run": 1},
        )
        run.runtime_id = runtime.id
        session.add(allow)
        session.flush()
        _set_mcp_snapshot(run, workspace, server, allow)
        approval = Approval(
            workspace_id=workspace_id,
            agent_run_id=run.id,
            approval_type="tool",
            risk_level="high",
            status="approved",
            created_at=datetime.now(UTC),
        )
        session.add(approval)
        session.flush()
        pending = PendingToolInvocationService(session, secrets)
        invocation = pending.create_or_get(
            PendingToolInvocationRequest(
                workspace_id=workspace_id,
                task_id=run.task_id,
                agent_run_id=run.id,
                approval_id=approval.id,
                tool_call_id="original-call",
                tool_name="inspect",
                tool_kind="mcp",
                arguments={},
                policy_decision={"decision": "require_approval"},
                idempotency_key="original-call",
            )
        )
        invocation.status = "approved"
        invocation_id = invocation.id
        context = AgentRuntimeContext(
            workspace_id=workspace_id,
            task_id=run.task_id,
            run_id=run_id,
            user_id=user_id,
            allowed_tools=("inspect",),
            tool_definitions=(_mcp_definition(server, allow),),
            runtime_binding=AgentRuntimeExecutionBinding(
                mode="team_runtime", workspace_runtime_id=runtime.id, runtime_space_id=None
            ),
        )
        session.commit()

    async def scenario():
        with BlockingIO(1, name="sdk-rpc-test") as io:
            database = DatabaseOperations(factory, io, lambda: None)
            executor = ScopedToolExecutor(database, settings, None)

            async def execute():
                return await executor.execute_tool(
                    context=context,
                    tool_name="inspect",
                    arguments={},
                    tool_call_id="original-call",
                    approval_granted=True,
                )

            task = asyncio.create_task(execute())
            async with asyncio.timeout(10):
                while True:

                    def queued(session):
                        job = session.scalar(select(SelfHostedMcpJob))
                        return job.id if job is not None else None

                    job_id = await database.run(queued)
                    if job_id is not None:
                        break
                    await asyncio.sleep(0.01)
            assert not task.done()
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

            def finish(session):
                pending = PendingToolInvocationService(session, secrets)
                invocation = pending.by_run_call(
                    workspace_id=workspace_id, run_id=run_id, tool_call_id="original-call"
                )
                assert invocation.status == "executing"
                assert invocation.encrypted_result is None
                pending.mark_stale_execution_outcome_unknown(
                    workspace_id=workspace_id, run_id=run_id
                )
                job = session.get(SelfHostedMcpJob, job_id)
                job.status = "completed"
                job.response_payload = {
                    "content": [{"type": "text", "text": "evidence"}],
                    "isError": False,
                }
                SelfHostedJobFinalizer(
                    session, SelfHostedEventRecorder(session)
                ).record_mcp_job_completion_for_run(session.get(AgentRun, run_id), job)

            await database.run(finish)
            recovered = await execute()
            assert recovered.status == "completed"
            assert recovered.output["content"][0]["text"] == "evidence"
            assert await execute() == recovered

    asyncio.run(scenario())
    with factory() as session:
        assert len(session.scalars(select(SelfHostedMcpJob)).all()) == 1
        invocation = PendingToolInvocationService(session, secrets).by_run_call(
            workspace_id=workspace_id, run_id=run_id, tool_call_id="original-call"
        )
        assert invocation.id == invocation_id
        assert invocation.status == "completed"
        assert invocation.attempt_count == 1
        assert session.scalar(select(McpToolCallLog)).status == "completed"


@pytest.mark.parametrize("direct", [False, True])
@pytest.mark.parametrize("outcome", ["completed", "failed"])
def test_runtime_rpc_completion_preserves_sdk_wait_and_settles_direct_workflow(direct, outcome):
    factory = _session_factory()
    workspace_id, run_id, _ = _seed_run(factory, status=RunStatus.WAITING_RUNTIME)
    with factory() as session:
        run = session.get(AgentRun, run_id)
        step = TaskStep(
            workspace_id=workspace_id,
            task_id=run.task_id,
            title="Inspect",
            order_index=1,
            dependencies={"node_type": "mcp" if direct else "agent"},
        )
        runtime = WorkspaceRuntime(
            workspace_id=workspace_id,
            name="RPC",
            runtime_provider="self_hosted",
            runtime_type="self_hosted",
            status="active",
            connection_status="online",
        )
        server = McpServer(
            workspace_id=workspace_id,
            name="RPC",
            server_type="stdio",
            connection={"command": "offline"},
        )
        session.add_all([step, runtime, server])
        session.flush()
        run.task_step_id = step.id
        job = SelfHostedMcpJob(
            workspace_id=workspace_id,
            workspace_runtime_id=runtime.id,
            agent_run_id=run_id,
            mcp_server_id=server.id,
            tool_name="inspect",
            tool_call_id="original-call",
            request_payload={},
            status=outcome,
            response_payload={"content": []} if outcome == "completed" else None,
            error_payload={"code": "offline_failure"} if outcome == "failed" else None,
        )
        session.add(job)
        session.flush()
        SelfHostedJobFinalizer(
            session, SelfHostedEventRecorder(session)
        ).record_mcp_job_completion_for_run(run, job)
        expected = "waiting_runtime"
        if direct:
            expected = "failed" if outcome == "failed" else "queued"
        assert run.status == expected
        assert "pending_tool_results" not in run.input
