"""Public runtime management uses current account permissions on every invocation."""

import time

import pytest

from opsmesh.framework.agents import AgentExecutionContext, AgentSpecification
from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase
from opsmesh.modules.accounts.internal.service import LocalAccountService
from opsmesh.modules.agents.internal.catalog import PersistentAgentDefinitionCatalog
from opsmesh.modules.audit.internal.journal import PersistentAuditJournal
from opsmesh.modules.runtime.api import RunApprovalPolicy, SubmitRunRequest
from opsmesh.modules.runtime.internal.extensions import RuntimeExtensionRegistry
from opsmesh.modules.runtime.internal.service import PersistentAgentRuntime
from opsmesh.modules.runtime.management import RunManagement
from opsmesh.modules.sessions.internal.service import ConversationService
from opsmesh.modules.sessions.management import ConversationManagement

from .test_runtime import ControlledBackend


@pytest.fixture
async def platform(tmp_path):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{(tmp_path / 'reviews.db').as_posix()}")
    accounts = LocalAccountService(database)
    audit = PersistentAuditJournal(database)
    sessions = ConversationService(database)

    async def validate(specification):
        assert specification.backend == "controlled"

    agents = PersistentAgentDefinitionCatalog(database, validate)
    extensions = RuntimeExtensionRegistry()
    backend = ControlledBackend(pause=True)
    extensions.register_backend("controlled", lambda: backend)

    async def context(view):
        async def tool(name, arguments, call_id):
            raise AssertionError("No external calls in management tests")

        return AgentExecutionContext(view.id, view.session_id, view.principal_id, tool)

    async def workspace(principal_id, workspace_id, *, write=False):
        raise OpsMeshError("FORBIDDEN", "No workspace permissions in fixture")

    runtime = PersistentAgentRuntime(database, extensions, context, session_claims=sessions)
    management = RunManagement(runtime, accounts, audit)
    conversations = ConversationManagement(sessions, accounts, agents, workspace, audit)
    await database.initialize()
    principals = {}
    for username in ("admin", "owner", "reviewer", "stranger"):
        principals[username] = await accounts.create_user(
            username, "test-password-123", is_admin=username == "admin"
        )
    try:
        yield accounts, runtime, management, conversations, principals, backend
    finally:
        await runtime.close()
        await database.close()


async def paused_run(platform, key="first"):
    _accounts, runtime, _manager, conversations, users, _backend = platform
    specification = AgentSpecification(id="assistant", name="Assistant", backend="controlled")
    session = await conversations.sessions.open(users["owner"].id, specification.ref)
    run = await runtime.submit(
        SubmitRunRequest(
            principal_id=users["owner"].id,
            session_id=session.id,
            agent=specification,
            input="hello",
            idempotency_key=key,
        )
    )
    return await runtime.execute(run.id)


async def test_service_ownership_applies_to_run_and_session_operations(platform):
    accounts, _runtime, manager, conversations, users, _backend = platform
    run = await paused_run(platform)
    assert await manager.list_runs(actor_id=users["stranger"].id) == []
    assert await conversations.list(actor_id=users["stranger"].id) == []
    assert (await manager.list_runs(actor_id=users["admin"].id))[0].id == run.id
    for operation in (
        manager.get_run(run.id, actor_id=users["stranger"].id),
        manager.cancel(run.id, actor_id=users["stranger"].id),
        manager.resume(run.id, run.revision, actor_id=users["stranger"].id),
        conversations.history(run.session_id, actor_id=users["stranger"].id),
        conversations.close(run.session_id, actor_id=users["stranger"].id),
        conversations.append_user(run.session_id, "forged", actor_id=users["stranger"].id),
    ):
        with pytest.raises(OpsMeshError) as denied:
            await operation
        assert denied.value.code == "FORBIDDEN"
    await accounts.update_user(users["owner"].id, active=False)
    with pytest.raises(OpsMeshError):
        await manager.get_run(run.id, actor_id=users["owner"].id)


async def test_designated_approver_reason_and_durable_decision(platform):
    _accounts, runtime, manager, _conversations, users, _backend = platform
    run = await paused_run(platform)
    policy = RunApprovalPolicy(
        approver_ids=(users["reviewer"].id,), allow_owner=False, require_reason=True
    )
    with pytest.raises(OpsMeshError) as cannot_assign:
        await manager.set_approval_policy(run.id, policy, run.revision, actor_id=users["owner"].id)
    assert cannot_assign.value.code == "FORBIDDEN"
    run = await manager.set_approval_policy(
        run.id, policy, run.revision, actor_id=users["admin"].id
    )
    assert await manager.approval_queue(actor_id=users["owner"].id) == []
    queue = await manager.approval_queue(actor_id=users["reviewer"].id)
    assert queue[0].run_id == run.id and queue[0].status == "pending"
    assert queue[0].expires_at > queue[0].requested_at
    for actor in (users["owner"].id, users["stranger"].id):
        with pytest.raises(OpsMeshError):
            await manager.decide(
                run.id, "approve1", True, run.revision, actor_id=actor, reason="reviewed"
            )
    with pytest.raises(OpsMeshError) as required:
        await manager.decide(run.id, "approve1", True, run.revision, actor_id=users["reviewer"].id)
    assert required.value.code == "INVALID_REQUEST"
    decided = await manager.decide(
        run.id,
        "approve1",
        True,
        run.revision,
        actor_id=users["reviewer"].id,
        reason="Verified requested operation",
    )
    assert decided.approval_reviews["approve1"].actor_id == users["reviewer"].id
    assert decided.approval_reviews["approve1"].reason == "Verified requested operation"
    restarted = PersistentAgentRuntime(
        runtime.runs.database, runtime.extensions, runtime.context_factory
    )
    assert (await restarted.get_run(run.id)).approval_reviews == decided.approval_reviews
    assert await manager.approval_queue(actor_id=users["reviewer"].id) == []
    assert (await manager.approval_queue(actor_id=users["reviewer"].id, include_decided=True))[
        0
    ].status == "approved"
    await manager.resume(run.id, decided.revision, actor_id=users["owner"].id)
    assert (await runtime.execute(run.id)).status == "completed"


async def test_expiry_blocks_execution_and_releases_session_without_backend_replay(platform):
    _accounts, runtime, manager, conversations, users, backend = platform
    run = await paused_run(platform)
    row = await runtime.runs.get(run.id)
    await runtime.runs.update(
        run.id, {**row.data, "approval_wait_started_at": time.time() - 86401}, row.revision
    )
    run = await runtime.get_run(run.id)
    assert (await manager.approval_queue(actor_id=users["owner"].id))[0].status == "expired"
    with pytest.raises(OpsMeshError) as expired:
        await manager.decide(run.id, "approve1", True, run.revision, actor_id=users["owner"].id)
    assert expired.value.code == "APPROVAL_EXPIRED"
    blocked = await runtime.expire_approvals(run.id, run.revision)
    assert blocked.status == "blocked" and blocked.error.code == "APPROVAL_EXPIRED"
    assert (await conversations.sessions.get(run.session_id)).active_run_id is None
    assert backend.calls == 1
    assert (await runtime.execute(run.id)).status == "blocked"
    assert backend.calls == 1
