import asyncio
from threading import Event
from uuid import uuid4

from sqlalchemy import select

from backend.app.agents.execution.contracts import AgentRunRequest, AgentRunResult
from backend.app.bootstrap.worker import build_worker_runner
from backend.app.orchestration.runs.live_async import buffered_live_events
from backend.app.orchestration.runs.live_events import RunLivePublisher
from backend.app.orchestration.runs.models import AgentRun
from backend.app.orchestration.tasks.events import TaskEventBus
from backend.app.runtime.queues.contracts import JobPayload, JobType
from backend.app.runtime.workers.models import WorkerRunnerConfig
from backend.app.shared.concurrency import BlockingIO
from backend.tests.test_worker_runner import _queue, _seed_run, _session_factory


def test_async_worker_history_uses_independent_transactions() -> None:
    factory, queue = _session_factory(), _queue()
    workspace_id, run_id, user_id = _seed_run(factory, slug="async-history")

    class HistorySDK:
        async def run(self, request: AgentRunRequest) -> AgentRunResult:
            assert request.session is not None
            assert await request.session.get_items() == []
            await request.session.add_items([{"role": "user", "content": "saved history"}])
            assert await request.session.get_items() == [
                {"role": "user", "content": "saved history"}
            ]
            return AgentRunResult(final_output="completed")

    queue.enqueue(
        JobPayload(
            workspace_id=workspace_id,
            resource_id=run_id,
            job_type=JobType.AGENT_RUN,
            requested_by_user_id=user_id,
            idempotency_key=str(run_id),
        )
    )
    runner = build_worker_runner(
        queue=queue,
        session_factory=factory,
        config=WorkerRunnerConfig(worker_id="async-history", blocking_io_concurrency=1),
        agent_runner=HistorySDK(),
    )
    assert runner.run_once()
    with factory() as session:
        assert session.get(AgentRun, run_id).status == "completed"


def test_slow_live_publication_keeps_event_loop_and_control_io_available() -> None:
    from typing import cast

    from backend.app.agents.execution.contracts import AgentRuntimeContext, AgentRuntimeProfile

    entered, release = Event(), Event()
    published = []

    class SlowBus:
        def publish(self, **values: object) -> str:
            entered.set()
            assert release.wait(5)
            published.append(values)
            return "event"

    async def scenario() -> None:
        workspace_id, task_id, run_id = uuid4(), uuid4(), uuid4()
        request = AgentRunRequest(
            agent_profile=AgentRuntimeProfile(
                id=uuid4(),
                workspace_id=workspace_id,
                version=1,
                name="Test",
                role="worker",
                instructions="Test",
                model="test",
            ),
            input_text="Test",
            context=AgentRuntimeContext(
                workspace_id=workspace_id,
                task_id=task_id,
                run_id=run_id,
            ),
            event_sink=RunLivePublisher(
                cast(TaskEventBus, SlowBus()),
                workspace_id,
                task_id,
                run_id,
                None,
                None,
            ),
        )
        with BlockingIO(1, name="test-live") as io, BlockingIO(1, name="test-control") as control:
            try:
                async with buffered_live_events(request, io):
                    request.event_sink.publish("output.reset", {})
                    while not entered.is_set():
                        await asyncio.sleep(0.01)
                    assert (
                        await asyncio.wait_for(control.run(lambda: "responsive"), 1) == "responsive"
                    )
                    release.set()
            finally:
                release.set()
        assert published[0]["workspace_id"] == workspace_id
        assert published[0]["task_id"] == task_id

    asyncio.run(scenario())


def test_async_worker_approval_resume_keeps_run_and_consumes_encrypted_state() -> None:
    from backend.app.agents.execution.contracts import (
        AgentRuntimeInterruption,
        AgentRuntimeResumeState,
    )
    from backend.app.orchestration.approvals.decisions import ApprovalDecisionService
    from backend.app.orchestration.approvals.models import Approval
    from backend.app.orchestration.runs.models import AgentRunStateSnapshot
    from backend.app.shared.config import Settings
    from backend.app.shared.security.secrets import SecretEncryptionService

    factory, queue = _session_factory(), _queue()
    workspace_id, run_id, user_id = _seed_run(factory, slug="async-resume")
    settings = Settings(environment="test")
    calls = []

    class PausingSDK:
        async def run(self, request: AgentRunRequest) -> AgentRunResult:
            calls.append(request.context.run_id)
            assert request.resume_state is None
            return AgentRunResult(
                final_output="",
                resume_state=AgentRuntimeResumeState(
                    provider="openai_agents",
                    serialized_state='{"checkpoint":"private-state"}',
                    schema_version="1.10",
                    sdk_version="0.17.2",
                ),
                interruptions=(
                    AgentRuntimeInterruption(
                        tool_call_id="resume-tool",
                        tool_name="write_artifact",
                        tool_kind="product",
                        arguments={"filename": "result.txt", "content": "test"},
                        policy_decision={"decision": "require_approval", "risk_level": "high"},
                    ),
                ),
            )

    class ResumingSDK:
        async def run(self, request: AgentRunRequest) -> AgentRunResult:
            calls.append(request.context.run_id)
            assert request.resume_state is not None
            assert "private-state" in request.resume_state.serialized_state
            assert request.approval_decisions[0].status == "approved"
            return AgentRunResult(final_output="resumed")

    def worker(sdk: object):
        return build_worker_runner(
            queue=queue,
            session_factory=factory,
            config=WorkerRunnerConfig(worker_id="resume-worker", blocking_io_concurrency=1),
            settings=settings,
            agent_runner=sdk,
        )

    queue.enqueue(
        JobPayload(
            workspace_id=workspace_id,
            resource_id=run_id,
            job_type=JobType.AGENT_RUN,
            requested_by_user_id=user_id,
            idempotency_key=str(run_id),
        )
    )
    assert worker(PausingSDK()).run_once()
    with factory() as session:
        run = session.get(AgentRun, run_id)
        assert run.status == "waiting_approval"
        snapshot = session.scalar(select(AgentRunStateSnapshot))
        assert "private-state" not in snapshot.encrypted_state
        approval = session.scalar(select(Approval).where(Approval.agent_run_id == run_id))
        ApprovalDecisionService(
            session,
            queue,
            SecretEncryptionService(
                secret=settings.credential_encryption_secret,
                key_id=settings.credential_encryption_key_id,
            ),
        ).approve(approval, user_id, "approve test checkpoint")
    assert worker(ResumingSDK()).run_once()
    assert calls == [run_id, run_id]
    with factory() as session:
        assert session.get(AgentRun, run_id).status == "completed"
        assert session.scalar(select(AgentRunStateSnapshot)).status == "consumed"
