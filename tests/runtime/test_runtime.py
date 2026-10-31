"""Runtime persistence, ownership and approval behavior without external services."""

import asyncio

import pytest

from opsmesh.framework.agents import (
    AgentBackendCapabilities,
    AgentExecutionContext,
    AgentRunCompleted,
    AgentRunInterrupted,
    AgentSpecification,
    ModelUsageObservation,
    PendingToolApproval,
    get_current_execution_context,
)
from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase
from opsmesh.modules.runtime.api import SubmitRunRequest
from opsmesh.modules.runtime.internal.extensions import RuntimeExtensionRegistry
from opsmesh.modules.runtime.internal.service import PersistentAgentRuntime


class ControlledBackend:
    id = "controlled"
    version = "1"
    features = AgentBackendCapabilities(streaming=True, approvals=True, resume=True)

    def __init__(self, *, pause=False, block=False):
        self.pause = pause
        self.block = block
        self.calls = 0
        self.started = asyncio.Event()

    async def run(self, spec, input, context, *, checkpoint=None, decisions=None, emit=None):
        self.calls += 1
        self.started.set()
        if self.block:
            await asyncio.Event().wait()
        if self.pause and checkpoint is None:
            return AgentRunInterrupted(
                pending=(PendingToolApproval(id="approve1", tool_name="transfer", agent_name="A"),),
                checkpoint={"step": 1},
                usage=ModelUsageObservation(total_tokens=5),
            )
        if checkpoint:
            assert decisions == {"approve1": True}
        return AgentRunCompleted(
            output={"answer": input}, usage=ModelUsageObservation(total_tokens=8)
        )


async def create_runtime(tmp_path, backend, **options):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}")
    registry = RuntimeExtensionRegistry()
    registry.register_backend(backend.id, lambda: backend)

    async def factory(view):
        async def invoke(name, args, call_id):
            raise AssertionError("Fake backend has no tool calls")

        return AgentExecutionContext(view.id, view.session_id, view.principal_id, invoke)

    runtime = PersistentAgentRuntime(database, registry, factory, **options)
    await database.initialize()
    return runtime, database


def request(key="one", session="session"):
    return SubmitRunRequest(
        principal_id="user",
        session_id=session,
        agent=AgentSpecification(id="agent", name="Agent", backend="controlled"),
        input="hello",
        idempotency_key=key,
    )


@pytest.mark.asyncio
async def test_idempotency_and_finalization_barrier(tmp_path):
    backend = ControlledBackend()
    seen = []

    async def finalize(view, outcome):
        seen.append(view.status)
        assert view.status == "finalizing"
        return {"artifact": "saved"}

    runtime, database = await create_runtime(tmp_path, backend, finalizer=finalize)
    try:
        run = await runtime.submit(request())
        assert (await runtime.submit(request())).id == run.id
        with pytest.raises(OpsMeshError, match="different input"):
            await runtime.submit(request().model_copy(update={"input": "different"}))
        result = await runtime.execute(run.id)
        assert result.status == "completed"
        assert result.metadata["artifact"] == "saved"
        assert seen == ["finalizing"]
        assert (await runtime.execute(run.id)).status == "completed"
        assert backend.calls == 1
        events = await database.pending_events()
        assert sum(e["type"] == "runtime.run.completed" for e in events) == 1
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_approval_is_durable_and_preserves_session_claim(tmp_path):
    runtime, database = await create_runtime(tmp_path, ControlledBackend(pause=True))
    try:
        run = await runtime.submit(request())
        waiting = await runtime.execute(run.id)
        assert waiting.status == "waiting_approval"
        second = await runtime.submit(request("two"))
        with pytest.raises(OpsMeshError) as error:
            await runtime.execute(second.id)
        assert error.value.code == "SESSION_BUSY"
        with pytest.raises(OpsMeshError):
            await runtime.decide(run.id, "approve1", True, waiting.revision - 1)
        decided = await runtime.decide(run.id, "approve1", True, waiting.revision)
        with pytest.raises(OpsMeshError):
            await runtime.decide(run.id, "approve1", True, decided.revision)
        await runtime.resume(run.id, decided.revision)
        # A newly built worker reads approvals/checkpoint from persistent storage.
        worker = PersistentAgentRuntime(database, runtime.extensions, runtime.context_factory)
        completed = await worker.execute(run.id)
        assert completed.status == "completed"
        assert completed.usage.total_tokens == 8
        assert not completed.has_checkpoint
        assert (await runtime.execute(second.id)).status == "waiting_approval"
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_cancel_propagates_and_releases_session(tmp_path):
    backend = ControlledBackend(block=True)
    runtime, database = await create_runtime(tmp_path, backend)
    try:
        run = await runtime.submit(request())
        task = runtime.start(run.id)
        await asyncio.wait_for(backend.started.wait(), 2)
        await runtime.cancel(run.id)
        with pytest.raises(asyncio.CancelledError):
            await task
        assert (await runtime.get_run(run.id)).status == "cancelled"
        assert await runtime.claims.list() == []
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_checkpoint_tampering_fails_closed(tmp_path):
    runtime, database = await create_runtime(tmp_path, ControlledBackend(pause=True))
    try:
        run = await runtime.submit(request())
        waiting = await runtime.execute(run.id)
        decided = await runtime.decide(run.id, "approve1", True, waiting.revision)
        await runtime.resume(run.id, decided.revision)
        record = await runtime.runs.get(run.id)
        data = dict(record.data)
        data["checkpoint"]["payload"] = {"step": 999}
        await runtime.runs.update(run.id, data, record.revision)
        result = await runtime.execute(run.id)
        assert result.status == "failed"
        assert result.error.code == "CHECKPOINT_INVALID"
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_session_owner_is_injected_not_duplicated(tmp_path):
    calls = []

    class Claims:
        async def claim(self, session_id, run_id):
            calls.append(("claim", session_id, run_id))

        async def release(self, session_id, run_id):
            calls.append(("release", session_id, run_id))

    runtime, database = await create_runtime(tmp_path, ControlledBackend(), session_claims=Claims())
    try:
        run = await runtime.submit(request())
        await runtime.execute(run.id)
        assert [entry[0] for entry in calls] == ["claim", "release"]
        assert await runtime.claims.list() == []
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_failed_finalizer_never_publishes_completed(tmp_path):
    async def finalize(view, outcome):
        raise OSError("Object store offline")

    runtime, database = await create_runtime(tmp_path, ControlledBackend(), finalizer=finalize)
    try:
        run = await runtime.submit(request())
        assert (await runtime.execute(run.id)).status == "failed"
        assert not any(
            e["type"] == "runtime.run.completed" for e in await database.pending_events()
        )
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_parallel_contexts_are_isolated_and_reset(tmp_path):
    seen = []

    class ContextBackend(ControlledBackend):
        async def run(self, spec, input, context, **kwargs):
            await asyncio.sleep(0.01)
            current = get_current_execution_context()
            assert current is context
            seen.append(current.run_id)
            return AgentRunCompleted(output=current.session_id)

    runtime, database = await create_runtime(tmp_path, ContextBackend())
    try:
        first = await runtime.submit(request("one", "one"))
        second = await runtime.submit(request("two", "two"))
        await asyncio.gather(runtime.execute(first.id), runtime.execute(second.id))
        assert set(seen) == {first.id, second.id}
        with pytest.raises(OpsMeshError) as error:
            get_current_execution_context()
        assert error.value.code == "CONTEXT_UNAVAILABLE"
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_terminal_stream_snapshot_is_replayable(tmp_path):
    runtime, database = await create_runtime(tmp_path, ControlledBackend())
    try:
        run = await runtime.submit(request())
        await runtime.execute(run.id)
        events = [event async for event in runtime.subscribe(run.id)]
        assert events[-1].kind == "completed"
        assert events[-1].payload["output"] == {"answer": "hello"}
        assert [e async for e in runtime.subscribe(run.id, events[-1].seq)] == []
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_recovery_never_replays_unknown_tool_effects(tmp_path):
    backend = ControlledBackend()
    runtime, database = await create_runtime(tmp_path, backend)
    try:
        run = await runtime.submit(request())
        active = await runtime._prepare_attempt(run.id)
        active = await runtime.runs.update(
            run.id, dict(active.data, lease_expires=0), active.revision
        )
        recovered = await runtime.recover(run.id, active.revision)
        assert recovered.status == "failed"
        assert recovered.error.code == "WORKER_LOST"
        assert backend.calls == 0
        assert await runtime.claims.list() == []
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_recovery_retries_finalizer_without_rerunning_model(tmp_path):
    backend = ControlledBackend()
    runtime, database = await create_runtime(tmp_path, backend)
    try:
        run = await runtime.submit(request())
        active = await runtime._prepare_attempt(run.id)
        outcome = AgentRunCompleted(output="durable answer")
        active = await runtime.runs.update(
            run.id,
            dict(
                active.data,
                status="finalizing",
                final_outcome=outcome.model_dump(mode="json"),
                lease_expires=0,
            ),
            active.revision,
        )
        queued = await runtime.recover(run.id, active.revision)
        assert queued.status == "queued"
        assert (await runtime.execute(run.id)).output == "durable answer"
        assert backend.calls == 0
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_scheduled_runs_wait_for_session_and_release_after_cancel(tmp_path):
    backend = ControlledBackend(block=True)
    runtime, database = await create_runtime(tmp_path, backend)
    try:
        first = await runtime.submit(request("one"))
        first_task = runtime.start(first.id)
        await asyncio.wait_for(backend.started.wait(), 2)
        second = await runtime.submit(request("two"))
        second_task = runtime.start(second.id)
        await asyncio.sleep(0.1)
        assert (await runtime.get_run(second.id)).status == "queued"
        backend.block = False
        await runtime.cancel(first.id)
        with pytest.raises(asyncio.CancelledError):
            await first_task
        assert (await asyncio.wait_for(second_task, 2)).status == "completed"
    finally:
        await runtime.close()
        await database.close()


@pytest.mark.asyncio
async def test_recovery_fences_old_worker_before_replacement_starts(tmp_path):
    runtime, database = await create_runtime(tmp_path, ControlledBackend())
    try:
        run = await runtime.submit(request())
        old = await runtime._prepare_attempt(run.id)
        active = await runtime.runs.update(
            run.id,
            dict(
                old.data,
                status="finalizing",
                lease_expires=0,
                final_outcome=AgentRunCompleted(output="saved").model_dump(mode="json"),
            ),
            old.revision,
        )
        await runtime.recover(run.id, active.revision)
        await runtime._cancel_attempt(old)
        assert (await runtime.get_run(run.id)).status == "queued"
        with pytest.raises(OpsMeshError) as error:
            await runtime._change(
                run.id, {"status": "completed"}, attempt_id=old.data["attempt_id"]
            )
        assert error.value.code == "OWNERSHIP_LOST"
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_persistent_launch_lease_and_exit_are_fenced(tmp_path):
    runtime, database = await create_runtime(tmp_path, ControlledBackend())
    try:
        run = await runtime.submit(request())
        assert await runtime.claim_worker_launch(run.id, "first")
        assert not await runtime.claim_worker_launch(run.id, "second")
        assert (await runtime.worker_exited(run.id, 125, launch_token="second")).status == "queued"
        record = await runtime.runs.get(run.id)
        await runtime.runs.update(
            run.id, dict(record.data, worker_launch_expires=0), record.revision
        )
        assert await runtime.claim_worker_launch(run.id, "second")
        assert not await runtime.renew_worker_launch(run.id, "first")
        with pytest.raises(OpsMeshError) as error:
            await runtime.execute(run.id, launch_token="first")
        assert error.value.code == "OWNERSHIP_LOST"
        assert (await runtime.worker_exited(run.id, 1, launch_token="first")).status == "queued"
        assert (await runtime.cancel(run.id, launch_token="first")).status == "queued"
        assert (await runtime.execute(run.id, launch_token="second")).status == "completed"
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_two_worker_launchers_cannot_claim_the_same_run(tmp_path):
    first, first_database = await create_runtime(tmp_path, ControlledBackend())
    second, second_database = await create_runtime(tmp_path, ControlledBackend())
    try:
        run = await first.submit(request())
        results = await asyncio.gather(
            first.claim_worker_launch(run.id, "first"),
            second.claim_worker_launch(run.id, "second"),
        )
        assert sum(results) == 1
        owner = "first" if results[0] else "second"
        assert (await second.execute(run.id, launch_token=owner)).status == "completed"
    finally:
        await first_database.close()
        await second_database.close()


@pytest.mark.asyncio
async def test_completed_runtime_task_is_not_retained(tmp_path):
    runtime, database = await create_runtime(tmp_path, ControlledBackend())
    try:
        run = await runtime.submit(request())
        assert (await runtime.start(run.id)).status == "completed"
        await asyncio.sleep(0)
        assert run.id not in runtime._tasks
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_stale_cancelled_worker_cannot_start_model_after_recovery(tmp_path):
    backend = ControlledBackend()
    runtime, database = await create_runtime(tmp_path, backend)
    try:
        run = await runtime.submit(request())
        active = await runtime._prepare_attempt(run.id)
        stale = await runtime.runs.update(
            run.id,
            dict(active.data, lease_expires=0, cancel_requested=True),
            active.revision,
        )
        assert (await runtime.recover(run.id, stale.revision)).status == "cancelled"
        context = await runtime._execution_context(active)
        with pytest.raises(OpsMeshError) as error:
            await runtime._invoke(active, context, backend)
        assert error.value.code == "OWNERSHIP_LOST"
        assert backend.calls == 0
    finally:
        await database.close()
