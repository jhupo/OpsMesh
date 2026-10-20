"""Real PostgreSQL state, RPC atomicity and current delegation authorization."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from opsmesh_sdk.manifest import PackageManifest, Permission
from sqlalchemy import event, func, select

from opsmesh.bootstrap.extensions import ExtensionScopes
from opsmesh.bootstrap.routers import ConfigurationExecutionScopeAdmission
from opsmesh.capabilities.models import CapabilityDefinition, CapabilityVersion
from opsmesh.extensions.access import authorize_invocation
from opsmesh.extensions.host import InvocationHost
from opsmesh.extensions.invocation import claim_invocation
from opsmesh.extensions.models import (
    ExtensionEvent,
    ExtensionInstance,
    ExtensionNamespace,
    HostCallReceipt,
    Installation,
    Invocation,
)
from opsmesh.extensions.schemas import StateMutation
from opsmesh.extensions.state import apply_mutations, get_namespace, read_value
from opsmesh.identity.provisioning import UserIdentityDirectory
from opsmesh.identity.quotas import service as quotas
from opsmesh.identity.quotas.models import UserStorageAccount
from opsmesh.identity.users.service import create_user
from opsmesh.shared.errors import ConflictError, ForbiddenError
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.temporal.dispatch.models import TemporalOutboxEntry

CIPHER = SecretCipher(key="JCxBK6PRTTz8-XJDf7ZEbEzcB2s3LFj8nRPODPwf6C0=", key_id="test")
SCOPES = ExtensionScopes(ConfigurationExecutionScopeAdmission(), CIPHER)


@pytest.fixture
def facts(database_sessions):
    from opsmesh.bootstrap.source_notifications import publish_source

    with database_sessions() as session, session.begin():
        user = create_user(session, email="s8-state@example.com", display_name="S8")
        quotas.ensure_ledger(session, user_id=user.id, storage_limit_bytes=100000)
        definition = CapabilityDefinition(
            type="extension", key="example", name="Example", created_by_user_id=user.id
        )
        session.add(definition)
        session.flush()
        version = CapabilityVersion(definition_id=definition.id, version=1, status="published")
        session.add(version)
        session.flush()
        permissions = ["host.state.write", "host.state.read", "host.events.publish"]
        manifest = PackageManifest(
            package_name="example",
            version="1",
            description="Example",
            permissions=tuple(Permission(name=value, reason="Acceptance") for value in permissions),
            published_events=("changed",),
        )
        installation = Installation(
            version_id=version.id,
            requested_by=user.id,
            package_digest="a" * 64,
            namespace="example",
            status="enabled",
            artifact="sha256:" + "b" * 64,
            manifest=manifest.model_dump(mode="json"),
            permission_ceiling=permissions,
        )
        session.add(installation)
        session.flush()
        instance = ExtensionInstance(
            installation_id=installation.id,
            owner_id=user.id,
            environment_id=uuid4(),
            permissions=permissions,
        )
        session.add(instance)
        session.flush()
        namespace = get_namespace(
            session, instance_id=instance.id, principal_id=user.id, scope_key=":"
        )
        invocation = Invocation(
            instance_id=instance.id,
            installation_id=installation.id,
            principal_id=user.id,
            namespace_id=namespace.id,
            export_id="operation",
            operation_id="operation:1",
            fingerprint="c" * 64,
            instance_revision=0,
            snapshot={"permissions": permissions, "namespace": "example"},
            input_ciphertext=CIPHER.encrypt_payload({}).ciphertext,
            deadline=datetime.now(UTC) + timedelta(minutes=5),
            status="started",
        )
        session.add(invocation)
        session.flush()
        identifiers = user.id, instance.id, installation.id, namespace.id, invocation.id
    registered = event.contains(ExtensionEvent, "after_insert", publish_source)
    if not registered:
        event.listen(ExtensionEvent, "after_insert", publish_source)
    try:
        yield identifiers
    finally:
        if not registered:
            event.remove(ExtensionEvent, "after_insert", publish_source)


def test_concurrent_cas_has_one_winner_and_storage_is_charged(database_sessions, facts):
    user_id, _, _, namespace_id, _ = facts

    def compete(value):
        try:
            with database_sessions() as session, session.begin():
                apply_mutations(
                    session,
                    namespace_id=namespace_id,
                    entries=[StateMutation(key="key", value=value, expected_version=0)],
                    limit_bytes=1000,
                )
            return True
        except ConflictError:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(compete, ["a", "b"])) == [False, True]
    with database_sessions() as session:
        account = session.get(UserStorageAccount, user_id)
        assert account.used_bytes > 0 and account.reserved_bytes == 0
        assert read_value(session, namespace_id, "key")["version"] == 1


def test_shared_extension_delegates_as_guest_and_membership_revokes_it(database_sessions, facts):
    from opsmesh.bootstrap.orchestration import ConfigurationDirectories, ConfigurationRunSources
    from opsmesh.orchestration.configurations.bindings.models import CapabilityBinding
    from opsmesh.orchestration.configurations.models import ConfigurationMember
    from opsmesh.orchestration.configurations.schemas import (
        CapabilitySelection,
        ConfigurationCreate,
        ConfigurationInvitationCreate,
        ConfigurationSpec,
    )
    from opsmesh.orchestration.configurations.service import (
        accept_invitation,
        create_configuration,
        invite_member,
    )
    from opsmesh.orchestration.contracts import ExecutionScope, ExecutionSubject
    from opsmesh.orchestration.definitions.contracts import FlowDefinitionSpec, FlowGraph, FlowNode
    from opsmesh.orchestration.definitions.schemas import DefinitionCreate
    from opsmesh.orchestration.definitions.service import create_definition, publish_definition
    from opsmesh.orchestration.runs.models import Run
    from opsmesh.orchestration.sessions.service import create_session
    from opsmesh.shared.errors import NotFoundError

    owner_id, instance_id, installation_id, _, invocation_id = facts
    admission = ConfigurationExecutionScopeAdmission()
    with database_sessions() as session, session.begin():
        guest = create_user(session, email="delegated-guest@example.com", display_name="Guest")
        users = UserIdentityDirectory()
        owner = users.load_identity(session, owner_id)
        actor = users.load_identity(session, guest.id)
        definition = create_definition(
            session,
            actor=owner,
            request=DefinitionCreate(
                name="Shared extension",
                spec=FlowDefinitionSpec(
                    graph=FlowGraph(
                        nodes=[
                            FlowNode(
                                key="wait", kind="wait", wait_kind="signal", signal_name="continue"
                            )
                        ]
                    )
                ),
            ),
        )
        revision = publish_definition(
            session, actor=owner, definition_id=definition.id, expected_revision=1
        )
        installation = session.get(Installation, installation_id)
        binding = CapabilityBinding(
            owner_user_id=owner_id,
            created_by=owner_id,
            name="shared-extension",
            version_id=installation.version_id,
            version_digest="a" * 64,
            configuration={"instance_id": str(instance_id)},
        )
        session.add(binding)
        session.flush()
        configuration = create_configuration(
            session,
            actor=owner,
            request=ConfigurationCreate(
                name="Shared",
                definition_revision_id=revision.id,
                specification=ConfigurationSpec(
                    capabilities={"extension": CapabilitySelection(binding_id=binding.id)}
                ),
            ),
            resources=ConfigurationDirectories(),
            admission=admission,
            idempotency_key="shared-delegation",
        )
        session.get(ExtensionInstance, instance_id).configuration_id = configuration.id
        invitation = invite_member(
            session,
            actor=owner,
            configuration_id=configuration.id,
            request=ConfigurationInvitationCreate(user_id=guest.id, permissions=["read", "use"]),
        )
        accept_invitation(session, actor=actor, invitation_id=invitation.id)
        namespace = get_namespace(
            session, instance_id=instance_id, principal_id=guest.id, scope_key=":"
        )
        parent = session.get(Invocation, invocation_id)
        parent.principal_id, parent.namespace_id = guest.id, namespace.id
        model_session = create_session(
            session,
            subject=ExecutionSubject(guest.id),
            scope=ExecutionScope(configuration_id=configuration.id),
            admission=admission,
        )
        run = Run(
            scope_id=configuration.id,
            principal_id=guest.id,
            owner_ref=f"configuration:{configuration.id}",
            session_id=model_session.id,
            sequence=1,
            request_ciphertext=CIPHER.encrypt_payload(
                {
                    "session_id": str(model_session.id),
                    "input": "Analyze logs",
                    "entry_agent": "main",
                    "parent_invocation_id": str(parent.id),
                    "agents": {
                        "main": {
                            "name": "Analyst",
                            "instructions": "Analyze",
                            "model": {"connection_id": str(uuid4()), "name": "test"},
                        }
                    },
                }
            ).ciphertext,
        )
        session.add(run)
        session.flush()
        sources = ConfigurationRunSources(CIPHER, admission, ExtensionScopes(admission, CIPHER))
        sources.authorize(session, run_id=run.id)
        assert run.principal_id == guest.id and model_session.principal_id == guest.id
        member = session.scalar(
            select(ConfigurationMember).where(
                ConfigurationMember.configuration_id == configuration.id,
                ConfigurationMember.user_id == guest.id,
            )
        )
        session.delete(member)
        session.flush()
        with pytest.raises(NotFoundError):
            sources.authorize(session, run_id=run.id)


def test_state_and_event_drafts_rollback_together_and_rpc_replays(database_sessions, facts):
    _, _, _, namespace_id, invocation_id = facts
    with database_sessions() as session:
        initial_dispatches = session.scalar(select(func.count()).select_from(TemporalOutboxEntry))

    class ForbiddenRemote:
        async def request(self, **kwargs):
            raise AssertionError("Local state must not leave its transaction")

    host = InvocationHost(
        database_sessions, UserIdentityDirectory(), SCOPES, CIPHER, ForbiddenRemote()
    )
    message = {
        "type": "request",
        "id": 1,
        "request": {
            "method": "POST",
            "path": "/host/state/batch",
            "body": {
                "entries": [{"key": "new", "value": 1}],
                "event_drafts": [{"event_type": "undeclared"}],
            },
        },
    }
    answer = asyncio.run(host.exchange(invocation_id, message))
    assert answer["status"] == 400
    with database_sessions() as session:
        assert not read_value(session, namespace_id, "new")["exists"]
        assert session.scalar(select(func.count()).select_from(ExtensionEvent)) == 0
        assert (
            session.scalar(select(func.count()).select_from(TemporalOutboxEntry))
            == initial_dispatches
        )
    message["request"]["body"]["event_drafts"] = [{"event_type": "changed"}]
    first = asyncio.run(host.exchange(invocation_id, message))
    assert first["status"] == 200
    assert asyncio.run(host.exchange(invocation_id, message)) == first
    with database_sessions() as session:
        assert read_value(session, namespace_id, "new")["version"] == 1
        assert session.scalar(select(func.count()).select_from(ExtensionEvent)) == 1
        assert session.scalar(select(func.count()).select_from(HostCallReceipt)) == 1
        assert (
            session.scalar(select(func.count()).select_from(TemporalOutboxEntry))
            == initial_dispatches + 1
        )


def test_revoke_configuration_stops_host_calls_and_recovery_does_not_rerun(
    database_sessions, facts
):
    _, instance_id, _, _, invocation_id = facts
    with database_sessions() as session, session.begin():
        recovered = claim_invocation(
            session, invocation_id, users=UserIdentityDirectory(), scopes=SCOPES
        )
        assert recovered.status == "needs_attention"
    with database_sessions() as session, session.begin():
        invocation = session.get(Invocation, invocation_id)
        invocation.status = "started"
        session.get(ExtensionInstance, instance_id).revision += 1
    with database_sessions() as session, pytest.raises(ForbiddenError):
        authorize_invocation(
            session,
            session.get(Invocation, invocation_id),
            users=UserIdentityDirectory(),
            scopes=SCOPES,
            permission="host.state.read",
        )


def test_hook_model_budget_refuses_before_starting_remote_effect(database_sessions, facts):
    _, instance_id, installation_id, _, invocation_id = facts
    permission = "host.models.complete"
    with database_sessions() as session, session.begin():
        instance = session.get(ExtensionInstance, instance_id)
        installation = session.get(Installation, installation_id)
        invocation = session.get(Invocation, invocation_id)
        instance.permissions = [*instance.permissions, permission]
        installation.permission_ceiling = [*installation.permission_ceiling, permission]
        invocation.snapshot = {
            **invocation.snapshot,
            "permissions": [*invocation.snapshot["permissions"], permission],
            "hook_budget": {"model_tokens": 0},
        }

    class NoRemote:
        async def request(self, **kwargs):
            raise AssertionError("A denied hook must not start model I/O")

    host = InvocationHost(database_sessions, UserIdentityDirectory(), SCOPES, CIPHER, NoRemote())
    result = asyncio.run(
        host.exchange(
            invocation_id,
            {
                "id": 1,
                "request": {
                    "method": "POST",
                    "path": "/host/models/complete",
                    "body": {"prompt": {"max_output_tokens": 32}},
                },
            },
        )
    )
    assert result["status"] == 409
    with database_sessions() as session:
        assert session.scalar(select(func.count()).select_from(HostCallReceipt)) == 0


def test_resident_drain_stages_stop_and_counts_until_confirmed(database_sessions, facts):
    from opsmesh.bootstrap.platform import active_work, operation_handlers
    from opsmesh.bootstrap.worker import admit_dispatch_start
    from opsmesh.extensions.models import ManagedDeployment
    from opsmesh.extensions.schemas import extension_work
    from opsmesh.temporal.dispatch.contracts import DispatchRequest, WorkflowStartOptions

    user_id, instance_id, installation_id, _, invocation_id = facts
    with database_sessions() as session, session.begin():
        session.get(Invocation, invocation_id).status = "completed"
        deployment = ManagedDeployment(
            instance_id=instance_id,
            instance_revision=0,
            artifact=session.get(Installation, installation_id).artifact,
            status="ready",
        )
        session.add(deployment)
        session.flush()
        identifier = deployment.id
        assert active_work(session)["extensions"] == 1
        actor = UserIdentityDirectory().load_identity(session, user_id)
        operation_handlers(CIPHER)["platform", "admission.drain"](
            session, {}, None, 100, actor, uuid4()
        )
        assert deployment.status == "stopping"
        assert active_work(session)["extensions"] == 1
    with database_sessions() as session:
        entry = session.scalar(
            select(TemporalOutboxEntry).where(TemporalOutboxEntry.operation == "signal")
        )
        assert str(identifier) in entry.workflow_id
        assert entry.operation == "signal"
        start = DispatchRequest(
            dispatch_id=uuid4(),
            workflow_id=f"extension-start_worker-{identifier}",
            workflow_name="ExtensionWorkflow",
            operation="start",
            execution=extension_work(identifier, "start_worker").model_dump(mode="json"),
            start_options=WorkflowStartOptions(),
        )
        assert admit_dispatch_start(session, start)
        assert not admit_dispatch_start(
            session,
            start.model_copy(
                update={
                    "execution": extension_work(invocation_id, "invoke").model_dump(mode="json")
                }
            ),
        )
    with database_sessions() as session, pytest.raises(ConflictError, match="drained"):
        claim_invocation(session, invocation_id, users=UserIdentityDirectory(), scopes=SCOPES)


def test_state_restore_rejects_even_idle_resident_deployment(database_sessions, facts):
    from opsmesh.extensions.models import ManagedDeployment
    from opsmesh.extensions.state_transfer import export_state, restore_state

    _, instance_id, installation_id, namespace_id, invocation_id = facts
    with database_sessions() as session, session.begin():
        instance = session.get(ExtensionInstance, instance_id)
        instance.status = "disabled"
        session.get(Invocation, invocation_id).status = "completed"
        session.add(
            ManagedDeployment(
                instance_id=instance_id,
                instance_revision=0,
                artifact=session.get(Installation, installation_id).artifact,
                status="ready",
            )
        )
    with database_sessions() as session, pytest.raises(ConflictError, match="resident"):
        snapshot = export_state(session, namespace_id)
        restore_state(session, namespace_id, snapshot, snapshot["revision"])


def test_state_restore_checks_revision_and_preserves_storage_on_rejection(database_sessions, facts):
    from opsmesh.extensions.state_transfer import export_state, restore_state

    user_id, instance_id, _, namespace_id, invocation_id = facts
    with database_sessions() as session, session.begin():
        session.get(ExtensionInstance, instance_id).status = "disabled"
        session.get(Invocation, invocation_id).status = "completed"
        apply_mutations(
            session,
            namespace_id=namespace_id,
            entries=[StateMutation(key="answer", value=42)],
            limit_bytes=10_000,
        )
        snapshot = export_state(session, namespace_id)
        used = session.get(UserStorageAccount, user_id).used_bytes
    with database_sessions() as session, pytest.raises(ConflictError), session.begin():
        restore_state(session, namespace_id, snapshot, int(snapshot["revision"]) + 1)
    with database_sessions() as session:
        assert read_value(session, namespace_id, "answer")["value"] == 42
        assert session.get(UserStorageAccount, user_id).used_bytes == used


def test_state_restore_uses_current_quota_and_restores_snapshot_values(database_sessions, facts):
    from opsmesh.extensions.state_transfer import export_state, restore_state

    user_id, instance_id, _, namespace_id, invocation_id = facts
    with database_sessions() as session, session.begin():
        session.get(ExtensionInstance, instance_id).status = "disabled"
        session.get(Invocation, invocation_id).status = "completed"
        apply_mutations(
            session,
            namespace_id=namespace_id,
            entries=[StateMutation(key="answer", value=42)],
            limit_bytes=10_000,
        )
        snapshot = export_state(session, namespace_id)
        original_usage = session.get(UserStorageAccount, user_id).used_bytes
        apply_mutations(
            session,
            namespace_id=namespace_id,
            entries=[StateMutation(key="answer", value="changed")],
            limit_bytes=10_000,
        )
        revision = session.get(ExtensionNamespace, namespace_id).revision
        assert restore_state(session, namespace_id, snapshot, revision) == 1
    with database_sessions() as session:
        assert read_value(session, namespace_id, "answer")["value"] == 42
        assert session.get(UserStorageAccount, user_id).used_bytes == original_usage


def test_configuration_cleanup_stops_only_its_resident_deployments(database_sessions, facts):
    from opsmesh.extensions.maintenance import stop_deployments
    from opsmesh.extensions.models import ManagedDeployment
    from tests.channels.helpers import flow_configuration

    user_id, instance_id, installation_id, _, invocation_id = facts
    with database_sessions() as session, session.begin():
        actor = UserIdentityDirectory().load_identity(session, user_id)
        target, _ = flow_configuration(session, actor=actor, name="Target")
        neighbor_configuration, _ = flow_configuration(session, actor=actor, name="Neighbor")
        instance = session.get(ExtensionInstance, instance_id)
        instance.configuration_id = target.id
        session.get(Invocation, invocation_id).status = "completed"
        neighbor = ExtensionInstance(
            installation_id=installation_id,
            owner_id=user_id,
            configuration_id=neighbor_configuration.id,
        )
        session.add(neighbor)
        session.flush()
        deployments = [
            ManagedDeployment(
                instance_id=item.id,
                instance_revision=0,
                artifact=session.get(Installation, installation_id).artifact,
                status="ready",
            )
            for item in (instance, neighbor)
        ]
        session.add_all(deployments)
        session.flush()
        target.status = "draining"
        assert stop_deployments(session, actor=actor, configuration_id=target.id) == 1
        assert [deployment.status for deployment in deployments] == ["stopping", "ready"]
        with pytest.raises(ConflictError, match="configuration"):
            SCOPES.admit(
                session,
                user_id=user_id,
                configuration_id=target.id,
                execution=True,
            )
        SCOPES.admit(
            session, user_id=user_id, configuration_id=neighbor_configuration.id, execution=True
        )
        session.flush()
        deployments[0].status = "stopped"
        session.flush()
        assert stop_deployments(session, actor=actor, configuration_id=target.id) == 0
