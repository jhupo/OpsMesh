"""Scope, execution input, cancellation and uncertain-capacity regressions."""

# ruff: noqa: F811
import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from agents.sandbox.types import ExecResult
from sqlalchemy import func, select

from opsmesh.bootstrap.capabilities import IdentityStorageQuotaLedger
from opsmesh.bootstrap.orchestration import ExecutionAssets, ExecutionModelConnections
from opsmesh.bootstrap.routers import ConfigurationScopeAdmission
from opsmesh.identity.context import CurrentUser
from opsmesh.identity.quotas import service as quotas
from opsmesh.identity.quotas.models import UserCapacityAccount, UserTokenAccount
from opsmesh.identity.users.service import create_user
from opsmesh.orchestration.contracts import ExecutionScope, ExecutionSubject
from opsmesh.orchestration.execution.contracts import ToolConfiguration, ToolInvocation
from opsmesh.orchestration.runs import reconciliation
from opsmesh.orchestration.runs.contracts import RunCompletion
from opsmesh.orchestration.runs.model_activities import ExecutionModelProvider, ModelActivities
from opsmesh.orchestration.runs.models import OperationReceipt, Run
from opsmesh.orchestration.runs.schemas import ReconcileRequest
from opsmesh.orchestration.runs.tool_activities import ToolActivities
from opsmesh.orchestration.runtime import files
from opsmesh.orchestration.runtime.models import Environment, EnvironmentOperationReceipt
from opsmesh.orchestration.runtime.sandbox import SandboxBackend, SandboxCommandCancelled
from opsmesh.orchestration.sessions.service import create_session
from opsmesh.platform.model_connections.service import create_connection, grant_connection
from opsmesh.resources.files import service as resource_files
from opsmesh.resources.files.access import ResourceReader, ScopeRef
from opsmesh.resources.files.models import ResourceRuntimePolicy
from opsmesh.shared.audit import AuditActor
from opsmesh.shared.errors import DomainError, ForbiddenError
from tests.execution.test_runs_postgres import (
    harness,  # noqa: F401
    model_claim,
)
from tests.execution.test_runtime_postgres import environment, runtime, stage


def set_concurrency(harness, limit):
    with harness.runs.sessions() as session, session.begin():
        quotas.set_capacity_limit(
            session,
            actor=harness.principal,
            user_id=harness.principal.user_id,
            kind="concurrency",
            limit_value=limit,
        )


def personal_request(harness):
    with harness.runs.sessions() as session, session.begin():
        record = create_session(
            session,
            subject=ExecutionSubject(harness.principal.user_id),
            scope=ExecutionScope(),
            admission=harness.runs.admission,
        )
    return harness.request.model_copy(update={"session_id": record.id})


def connection(harness, actor):
    with harness.runs.sessions() as session, session.begin():
        configured = create_connection(
            session,
            actor=harness.principal,
            cipher=harness.runs.cipher,
            name="review-model",
            api_key="local-test-only",
            base_url="https://api.openai.com/v1",
            default_model="model-test",
            models=["model-test"],
        )
        grant_connection(
            session,
            actor=harness.principal,
            connection_id=configured.id,
            user_id=actor.user_id,
            models=["model-test"],
            is_default=True,
        )
        return configured.id


def test_personal_run_cannot_borrow_another_users_model(harness):
    with harness.runs.sessions() as session, session.begin():
        user = create_user(session, email="other-model@example.com", display_name="Other")
    owner = CurrentUser(user.id, user.email, user.display_name, "active", frozenset())
    connection_id = connection(harness, owner)
    agent = harness.request.agents["main"]
    harness.request = harness.request.model_copy(
        update={
            "agents": {
                "main": agent.model_copy(
                    update={
                        "model": agent.model.model_copy(update={"connection_id": connection_id})
                    }
                )
            }
        }
    )
    run = harness.submit()
    harness.claim(run)
    provider = ExecutionModelProvider(
        harness.runs.sessions,
        harness.runs.bindings,
        harness.runs.admission,
        ExecutionModelConnections(harness.runs.cipher, {"model-test": 1000}),
        harness.runs.cipher,
    )
    with pytest.raises(ForbiddenError):
        model_claim(ModelActivities(provider, run.id, "main"))
    with harness.runs.sessions() as session:
        assert session.scalar(select(func.count()).select_from(OperationReceipt)) == 0
        assert session.get(UserTokenAccount, harness.principal.user_id).reserved_tokens == 0


def test_user_capacity_applies_across_sessions(harness):
    set_concurrency(harness, 0)
    assert harness.claim(harness.submit()) is None
    assert harness.claim(harness.submit("other-session", personal_request(harness))) is None


def test_user_quota_refusal_rolls_back_model_token_reservation(harness):
    connection_id = connection(harness, harness.principal)
    with harness.runs.sessions() as session, session.begin():
        quotas.set_limits(
            session,
            actor=AuditActor.from_user(harness.principal),
            user_id=harness.principal.user_id,
            token_limit=1000,
        )
    agent = harness.request.agents["main"]
    request = harness.request.model_copy(
        update={
            "agents": {
                "main": agent.model_copy(
                    update={
                        "model": agent.model.model_copy(update={"connection_id": connection_id})
                    }
                )
            }
        }
    )
    run = harness.submit(request=request)
    assert harness.claim(run) is not None
    provider = ExecutionModelProvider(
        harness.runs.sessions,
        harness.runs.bindings,
        harness.runs.admission,
        ExecutionModelConnections(harness.runs.cipher, {"model-test": 1000}),
        harness.runs.cipher,
    )
    with pytest.raises(DomainError) as denied:
        model_claim(ModelActivities(provider, run.id, "main"))
    assert denied.value.code == "quota_exceeded"
    with harness.runs.sessions() as session:
        assert session.get(UserTokenAccount, harness.principal.user_id).reserved_tokens == 0
        assert session.scalar(select(func.count()).select_from(OperationReceipt)) == 0


def test_unresolved_runtime_effect_also_retains_user_slot(harness):
    run = harness.submit()
    assert harness.claim(run) is not None
    env_id = environment(harness)
    command = stage(harness, env_id, "pending-output", action="read", content=b"")
    with harness.runs.sessions() as session, session.begin():
        receipt = session.get(
            EnvironmentOperationReceipt, (harness.principal.user_id, command.operation_id)
        )
        receipt.run_id = run.id
    assert (
        harness.runs._finish(RunCompletion(run_id=run.id, status="cancelled")) == "needs_attention"
    )
    with harness.runs.sessions() as session:
        assert (
            session.get(
                UserCapacityAccount, (harness.principal.user_id, "concurrency")
            ).reserved_value
            == 1
        )


@pytest.fixture
def input_file(harness, tmp_path):
    with harness.runs.sessions() as session, session.begin():
        owner = create_user(session, email="input-owner@example.com", display_name="Owner")
        quotas.ensure_ledger(session, user_id=owner.id)
        reader = ResourceReader(owner.id)
        actor = AuditActor(actor_type="user", actor_id=str(owner.id))
        directory = resource_files.create_directory(
            session,
            owner_id=owner.id,
            path="input",
            reader=reader,
            scope=ScopeRef(),
            admission=ConfigurationScopeAdmission(),
        )
        file = resource_files.create_file(
            session,
            owner_id=owner.id,
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
            actor=actor,
            ledger=IdentityStorageQuotaLedger(),
            storage_root=tmp_path,
            idempotency_key="input",
        )
        resource_files.set_runtime_policy(
            session,
            file_id=file.id,
            reader=reader,
            actor=actor,
            use_in_execution=True,
            frozen_input_versions=[version.version],
            relative_paths=["input.txt"],
            output_declarations=[],
        )
        resource_files.grant_access(
            session,
            file_id=file.id,
            grantee_id=harness.principal.user_id,
            permissions=["use_in_execution"],
            reader=reader,
            actor=actor,
        )
    return file, version, reader, actor


def read_input(harness, tmp_path, input_file, **changes):
    file, version, _, _ = input_file
    arguments = dict(
        subject=ExecutionSubject(harness.principal.user_id),
        scope=ExecutionScope(),
        ref=f"file:{file.id}:{version.version}",
        digest=version.digest,
        destination="input.txt",
    )
    arguments.update(changes)
    with harness.runs.sessions() as session:
        return ExecutionAssets(tmp_path, ConfigurationScopeAdmission()).read_asset(
            session, **arguments
        )


@pytest.mark.parametrize("permission", ["read", None])
def test_ordinary_read_or_revoked_grant_never_stages_input(
    harness, tmp_path, input_file, permission
):
    file, _, owner, actor = input_file
    with harness.runs.sessions() as session, session.begin():
        if permission:
            resource_files.grant_access(
                session,
                file_id=file.id,
                grantee_id=harness.principal.user_id,
                permissions=[permission],
                reader=owner,
                actor=actor,
            )
        else:
            grant = resource_files.list_grants(session, file_id=file.id, reader=owner)[0]
            resource_files.revoke_access(
                session,
                file_id=file.id,
                grant_id=grant.id,
                reader=owner,
            )
    with pytest.raises(ForbiddenError):
        read_input(harness, tmp_path, input_file)


@pytest.mark.parametrize("change", ["disabled", "version", "path", "missing"])
def test_execution_input_checks_current_policy_before_storage_io(
    harness, tmp_path, input_file, change, monkeypatch
):
    file, _, _, _ = input_file
    with harness.runs.sessions() as session, session.begin():
        policy = session.scalar(
            select(ResourceRuntimePolicy).where(ResourceRuntimePolicy.file_id == file.id)
        )
        if change == "disabled":
            policy.use_in_execution = False
        elif change == "version":
            policy.frozen_input_versions = []
        elif change == "path":
            policy.relative_paths = ["elsewhere.txt"]
        else:
            session.delete(policy)

    def forbidden_io(*args, **kwargs):
        raise AssertionError("Unauthorized input must not reach blob storage")

    monkeypatch.setattr(resource_files, "read_content", forbidden_io)
    with pytest.raises(ForbiddenError):
        read_input(harness, tmp_path, input_file)


def test_execution_only_grant_allows_frozen_input_but_not_ordinary_read(
    harness, tmp_path, input_file
):
    assert read_input(harness, tmp_path, input_file) == b"frozen input"
    with harness.runs.sessions() as session, pytest.raises(ForbiddenError):
        resource_files.read_version_content(
            session,
            file_id=input_file[0].id,
            version_number=1,
            reader=ResourceReader(harness.principal.user_id),
            storage_root=tmp_path,
        )
    with pytest.raises(DomainError):
        read_input(harness, tmp_path, input_file, scope=ExecutionScope(configuration_id=uuid4()))


class DelayedCommand:
    def __init__(self, fail=False):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.fail = fail
        self.cancelled = False

    async def exec(self, *command, **options):
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        if self.fail:
            raise TimeoutError("No proof that Docker stopped the command")
        return ExecResult(stdout=b"done", stderr=b"", exit_code=0)


def test_cancellation_waits_for_command_completion_even_when_cancelled_twice():
    async def scenario():
        native = DelayedCommand()
        task = asyncio.create_task(SandboxBackend().execute(native, "command", {"command": ["x"]}))
        await native.started.wait()
        for _ in range(2):
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done() and not native.cancelled
        native.release.set()
        with pytest.raises(SandboxCommandCancelled) as result:
            await task
        assert result.value.result == {"stdout": "done", "stderr": "", "exit_code": 0}

    asyncio.run(scenario())


@pytest.mark.parametrize("fail", [False, True])
def test_runtime_cancellation_records_known_completion_or_uncertainty(harness, fail):
    async def scenario():
        env_id = environment(harness)
        command = stage(harness, env_id, "cancel-command", action="command", command=["x"])
        backend = SandboxBackend()
        native = DelayedCommand(fail)
        backend.resume = AsyncMock(return_value=native)
        task = asyncio.create_task(runtime(harness, backend).execute(command))
        await native.started.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        native.release.set()
        with pytest.raises(TimeoutError if fail else asyncio.CancelledError):
            await task
        with harness.runs.sessions() as session:
            receipt = session.get(
                EnvironmentOperationReceipt, (harness.principal.user_id, command.operation_id)
            )
            env = session.get(Environment, env_id)
            assert receipt.status == ("uncertain" if fail else "completed")
            assert env.status == ("needs_attention" if fail else "ready")
            assert (env.active_operation_id is None) is not fail

    asyncio.run(scenario())


def test_cancelled_tool_is_accounted_once_after_native_completion(harness):
    env_id = environment(harness)
    agent = harness.request.agents["main"].model_copy(
        update={
            "tools": [
                ToolConfiguration(
                    name="command",
                    command=["echo"],
                    input_schema={"type": "object"},
                )
            ]
        }
    )
    request = harness.request.model_copy(
        update={"environment_id": env_id, "agents": {"main": agent}}
    )
    run = harness.submit(request=request)
    harness.claim(run)
    invocation = ToolInvocation(run_id=run.id, agent_key="main", tool_name="command", call_id="one")

    async def scenario():
        backend = SandboxBackend()
        native = DelayedCommand()
        backend.resume = AsyncMock(return_value=native)
        tools = ToolActivities(harness.runs, backend)
        task = asyncio.create_task(tools.invoke(invocation))
        await native.started.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        native.release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert (await tools.invoke(invocation))["stdout"] == "done"

    asyncio.run(scenario())
    assert harness.runs._finish(RunCompletion(run_id=run.id, status="cancelled")) == "cancelled"
    with harness.runs.sessions() as session:
        receipt = session.scalar(select(OperationReceipt))
        assert receipt.status == "completed" and receipt.accounted
        assert session.get(Environment, env_id).active_operation_id is None
        assert session.get(Run, run.id).usage["tool_invocations"] == 1


def test_uncertain_effect_holds_user_slot_until_reconciliation(harness):
    set_concurrency(harness, 1)
    run = harness.submit(request=personal_request(harness))
    assert harness.claim(run) is not None
    model = harness.model(run)
    model_claim(model)
    model.complete("model-operation", None, None)
    assert (
        harness.runs._finish(RunCompletion(run_id=run.id, status="cancelled")) == "needs_attention"
    )
    next_run = harness.submit("next-session", personal_request(harness))
    assert harness.claim(next_run) is None
    with harness.runs.sessions() as session, session.begin():
        assert (
            session.get(
                UserCapacityAccount, (harness.principal.user_id, "concurrency")
            ).reserved_value
            == 1
        )
        current = session.get(Run, run.id)
        reconciliation.reconcile(
            session,
            run_id=run.id,
            actor=harness.principal,
            cipher=harness.runs.cipher,
            bindings=harness.runs.bindings,
            request=ReconcileRequest(
                command_id=uuid4(),
                expected_revision=current.event_sequence,
                evidence={"provider": "confirmed-not-executed"},
                operations=[{"operation_id": "model-operation", "outcome": "not_executed"}],
            ),
        )
    assert harness.claim(next_run) is not None


def test_transfer_uses_bounded_remote_read_and_rejects_oversize_before_base64():
    class Native:
        async def read(self, path):
            raise AssertionError("Native read buffers the whole file")

        async def exec(self, *command, **options):
            assert command[-1] == str(files.MAX_FILE_BYTES)
            assert options["shell"] is False
            return ExecResult(stdout=b"x" * (files.MAX_FILE_BYTES + 1), stderr=b"", exit_code=0)

    with pytest.raises(ValueError, match="transfer limit"):
        asyncio.run(files.read_file(Native(), "output.bin"))
