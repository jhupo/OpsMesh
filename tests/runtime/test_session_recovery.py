"""Terminal Run cleanup remains recoverable across crashes and concurrent workers."""

import asyncio

import pytest

from opsmesh.framework.agents import AgentExecutionContext, AgentSpecification
from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase
from opsmesh.modules.runtime.api import SubmitRunRequest
from opsmesh.modules.runtime.internal.extensions import RuntimeExtensionRegistry
from opsmesh.modules.runtime.internal.service import PersistentAgentRuntime
from opsmesh.modules.sessions.internal.service import ConversationService

from .test_runtime import ControlledBackend, create_runtime, request


@pytest.fixture
async def platform(tmp_path):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{(tmp_path / 'sessions.db').as_posix()}")
    sessions = ConversationService(database)
    registry = RuntimeExtensionRegistry()
    backend = ControlledBackend()
    registry.register_backend(backend.id, lambda: backend)

    async def context(view):
        async def invoke(*args):
            raise AssertionError("No tool calls expected")

        return AgentExecutionContext(view.id, view.session_id, view.principal_id, invoke)

    def worker():
        return PersistentAgentRuntime(
            database,
            registry,
            context,
            session_claims=sessions,
            session_claim_reader=sessions.active_claims,
        )

    runtime = worker()
    await database.initialize()
    session = await sessions.open("owner", "agent@1")
    submission = SubmitRunRequest(
        principal_id="owner",
        session_id=session.id,
        agent=AgentSpecification(id="agent", name="Agent", backend=backend.id),
        input="hello",
        idempotency_key="first",
    )
    try:
        yield runtime, sessions, submission, worker
    finally:
        await database.close()


async def test_release_failure_after_completion_recovers_on_new_worker(platform, monkeypatch):
    runtime, sessions, submission, worker = platform
    run = await runtime.submit(submission)
    original = sessions.release

    async def fail_once(*args):
        raise OpsMeshError("STORAGE_UNAVAILABLE", "Temporary release failure")

    monkeypatch.setattr(sessions, "release", fail_once)
    with pytest.raises(OpsMeshError) as failed:
        await runtime.execute(run.id)
    assert failed.value.code == "STORAGE_UNAVAILABLE"
    assert (await runtime.get_run(run.id)).status == "completed"
    assert (await sessions.get(submission.session_id)).active_run_id == run.id
    monkeypatch.setattr(sessions, "release", original)
    restarted = worker()
    await restarted.recover_stale()
    assert await sessions.active_claims() == {}
    next_run = await restarted.submit(submission.model_copy(update={"idempotency_key": "second"}))
    assert (await restarted.execute(next_run.id)).status == "completed"


@pytest.mark.parametrize("status", ["completed", "failed", "blocked", "cancelled"])
@pytest.mark.parametrize("operation", ["recover_stale", "execute", "cancel", "reconcile_session"])
async def test_every_terminal_state_can_repair_a_legacy_claim(platform, status, operation):
    runtime, sessions, submission, worker = platform
    run = await runtime.submit(submission)
    await sessions.claim(submission.session_id, run.id)
    row = await runtime.runs.get(run.id)
    # Crash snapshot: terminal status committed, without any cleanup marker or release.
    await runtime.runs.update(run.id, {**row.data, "status": status}, row.revision)
    restarted = worker()
    if operation == "recover_stale":
        await restarted.recover_stale()
    else:
        await getattr(restarted, operation)(run.id)
    assert (await sessions.get(submission.session_id)).active_run_id is None
    assert (await restarted.get_run(run.id)).status == status


async def test_repeated_terminal_cleanup_cannot_release_replacement_run(platform):
    runtime, sessions, submission, worker = platform
    run = await runtime.submit(submission)
    await runtime.execute(run.id)
    replacement = await runtime.submit(submission.model_copy(update={"idempotency_key": "second"}))
    await sessions.claim(submission.session_id, replacement.id)
    other = worker()
    await asyncio.gather(
        runtime.reconcile_session(run.id),
        other.reconcile_session(run.id),
        runtime.cancel(run.id),
        other.execute(run.id),
        other.recover_stale(),
    )
    assert (await sessions.get(submission.session_id)).active_run_id == replacement.id


async def test_late_claim_after_terminal_cleanup_is_reconciled_again(platform):
    runtime, sessions, submission, worker = platform
    run = await runtime.submit(submission)
    await runtime.cancel(run.id)
    assert await sessions.active_claims() == {}
    # A worker read queued before cancellation, acquired its claim late, then crashed.
    await sessions.claim(submission.session_id, run.id)
    await worker().recover_stale()
    assert await sessions.active_claims() == {}


async def test_concurrent_cleanup_is_idempotent(platform):
    runtime, sessions, submission, worker = platform
    run = await runtime.submit(submission)
    await runtime.cancel(run.id)
    await sessions.claim(submission.session_id, run.id)
    await asyncio.gather(*(worker().reconcile_session(run.id) for _ in range(4)))
    assert await sessions.active_claims() == {}


async def test_internal_claim_cleanup_does_not_hide_storage_errors(tmp_path, monkeypatch):
    runtime, database = await create_runtime(tmp_path, ControlledBackend())
    try:
        run = await runtime.submit(request())
        original = runtime.claims.delete

        async def unavailable(*args):
            raise OpsMeshError("STORAGE_UNAVAILABLE", "Temporary release failure")

        monkeypatch.setattr(runtime.claims, "delete", unavailable)
        with pytest.raises(OpsMeshError) as failed:
            await runtime.execute(run.id)
        assert failed.value.code == "STORAGE_UNAVAILABLE"
        assert len(await runtime.claims.list()) == 1
        monkeypatch.setattr(runtime.claims, "delete", original)
        await runtime.recover_stale()
        assert await runtime.claims.list() == []
    finally:
        await database.close()
