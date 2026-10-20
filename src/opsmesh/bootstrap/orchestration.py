"""Composition-owned state and explicit execution operations."""

from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from opsmesh_sdk.host.jobs import JobSubmission
from opsmesh_sdk.values import FlowRunRef
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.extensions.access import (
    ScopeAuthorization,
    authorize_installation,
    authorize_instance,
    authorize_invocation,
    installation_viewer,
    load_installation,
)
from opsmesh.extensions.delegation import current_call
from opsmesh.extensions.drain import drain_configuration as drain_extensions
from opsmesh.extensions.invocation import load_invocation
from opsmesh.extensions.models import ExtensionInstance, Invocation
from opsmesh.identity.context import CurrentUser
from opsmesh.identity.errors import AuthenticationError
from opsmesh.identity.quotas import service as quota_service
from opsmesh.identity.users import service as users
from opsmesh.identity.users.models import UserProfile
from opsmesh.identity.users.service import load_identity
from opsmesh.orchestration.configurations.closing import drain_configuration as drain_execution
from opsmesh.orchestration.configurations.models import ConfigurationClosure, UseConfiguration
from opsmesh.orchestration.configurations.service import authorize_run_configuration
from opsmesh.orchestration.contracts import (
    CapacityLedger,
    ExecutionBindings,
    ExecutionScope,
    ExecutionSubject,
    RunAssets,
    ScopeAdmission,
    TokenLedger,
    configuration_access_for,
)
from opsmesh.orchestration.definitions.contracts import FlowDefinitionSpec
from opsmesh.orchestration.definitions.service import load_revision
from opsmesh.orchestration.execution.contracts import ExecutionRequest, ModelConnection
from opsmesh.orchestration.runs.activities import FlowActivities
from opsmesh.orchestration.runs.admission import reserve_root_bound
from opsmesh.orchestration.runs.model_operations import ModelOperation
from opsmesh.orchestration.runs.models import FlowRun, Run, StepAttempt
from opsmesh.orchestration.runs.schemas import FlowControl, FlowRunCreate
from opsmesh.orchestration.runs.service import control_run, flow_run_view, start_run
from opsmesh.orchestration.runs.usage import audit_actor
from opsmesh.orchestration.sessions.models import SessionRecord
from opsmesh.platform.model_connections.service import resolve_connection, resolve_model_secret
from opsmesh.platform.operations.admission import is_open
from opsmesh.resources.artifacts import service as artifacts
from opsmesh.resources.files import service as files
from opsmesh.resources.files import service as resource_files
from opsmesh.resources.files.access import RejectingScopeAdmission, ResourceReader, ScopeRef
from opsmesh.resources.files.access import ScopeAdmission as ResourceScopeAdmission
from opsmesh.resources.files.models import ResourceDirectory
from opsmesh.shared.approvals import ApprovalTarget
from opsmesh.shared.db.idempotency import run_idempotent_create
from opsmesh.shared.errors import ConflictError, ForbiddenError
from opsmesh.shared.files import validate_relative_path
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.temporal.dispatch.outbox import stage_signal


def source_flow(session: Session, reference: UUID, cipher: SecretCipher) -> FlowRun | None:
    visited: set[UUID] = set()
    while reference not in visited:
        visited.add(reference)
        step = session.scalar(select(StepAttempt).where(StepAttempt.child_id == reference))
        if step:
            return session.get(FlowRun, step.run_id)
        model = session.get(ModelOperation, reference)
        if model:
            reference = model.source_ref
            continue
        invocation = session.get(Invocation, reference)
        if invocation and invocation.parent_id:
            reference = invocation.parent_id
            continue
        if invocation:
            parent = invocation.snapshot.get("run_id")
            if parent:
                reference = UUID(str(parent))
                continue
        run = session.get(Run, reference)
        if run:
            parent = cipher.decrypt_payload(run.request_ciphertext).get("parent_invocation_id")
            if parent:
                reference = UUID(str(parent))
                continue
        return None
    raise ConflictError("The execution source chain contains a cycle")


class FlowOperationLimit:
    def __init__(self, cipher: SecretCipher) -> None:
        self.cipher = cipher

    def __call__(
        self,
        session: Session,
        *,
        operation_id: str,
        run_ref: str,
        tokens: int,
    ) -> None:
        flow = source_flow(session, UUID(run_ref), self.cipher)
        if flow:
            reserve_root_bound(
                session,
                root_id=flow.root_id,
                operation_id=operation_id,
                tokens=tokens,
            )


def host_jobs(
    runtime: FlowActivities, *, invocation_id: UUID, operation_id: str, request: dict[str, Any]
) -> dict[str, Any]:
    with runtime.sessions() as session, session.begin():
        invocation = load_invocation(session, invocation_id)
        parent = source_flow(session, invocation.id, runtime.cipher)
        if parent:
            from opsmesh.orchestration.runs.access import load_flow_run

            controller = runtime.actor(session, parent)
            load_flow_run(session, actor=controller, run_id=parent.id, write=True, lock=True)
        actor, _, _ = authorize_invocation(
            session, invocation, users=runtime.users, scopes=runtime.scopes, permission="host.jobs"
        )
        current_invocation = session.get(
            Invocation, invocation_id, with_for_update=True, populate_existing=True
        )
        assert current_invocation is not None
        invocation = current_invocation
        actor, _, _ = authorize_invocation(
            session, invocation, users=runtime.users, scopes=runtime.scopes, permission="host.jobs"
        )
        path, method = request["path"], request["method"]
        if path == "/host/jobs" and method == "POST":
            submission = JobSubmission.model_validate(request.get("body"))
            if submission.parent_ref is not None:
                raise ConflictError("A job parent is determined by its host Invocation")
            revision, definition = load_revision(
                session, actor=actor, revision_id=UUID(submission.definition_revision), use=True
            )
            if definition.kind != "flow":
                raise ConflictError("A host job must target a published Flow definition")
            spec = FlowDefinitionSpec.model_validate(revision.spec)
            roots = [node.key for node in spec.graph.nodes if not node.depends_on]
            if submission.entry_step != "flow" and roots != [submission.entry_step]:
                raise ConflictError(
                    "The job entry must select the fixed flow or its sole published entry"
                )
            snapshot = invocation.snapshot
            configuration_id = snapshot.get("configuration_id")
            if not configuration_id:
                raise ConflictError("A host job requires a configured use object")
            _, fixed_revision = configuration_access_for(session).flow_definition(
                session,
                actor=actor,
                configuration_id=UUID(str(configuration_id)),
            )
            if fixed_revision != revision.id:
                raise ConflictError(
                    "A host job must use the Flow revision fixed by its Invocation configuration"
                )
            parent = source_flow(session, invocation.id, runtime.cipher)
            record = start_run(
                session,
                actor=actor,
                request=FlowRunCreate(
                    revision_id=revision.id,
                    configuration_id=UUID(str(configuration_id)),
                    input=submission.input,
                ),
                idempotency_key=f"invocation:{invocation.id}:{submission.idempotency_key}",
                cipher=runtime.cipher,
                parent=parent,
                parent_invocation_id=invocation.id,
            )
            runtime.validate_start(session, actor=actor, run=record)
            return {
                "status": 202,
                "body": FlowRunRef(flow_run_id=str(record.id)).model_dump(mode="json"),
            }
        parts = path.split("/")
        if len(parts) != 5 or parts[:3] != ["", "host", "jobs"]:
            raise ConflictError("Unknown host job operation")
        record = session.get(FlowRun, UUID(parts[3]))  # type: ignore[assignment]
        if record is None or record.parent_invocation_id != invocation.id:
            raise ConflictError("The job was not submitted by this Invocation")
        if method == "GET" and parts[4] == "progress":
            return {
                "status": 200,
                "body": flow_run_view(record, runtime.cipher).model_dump(mode="json"),
            }
        if method == "POST" and parts[4] == "cancel":
            control_run(
                session,
                actor=actor,
                run_id=record.id,
                cipher=runtime.cipher,
                request=FlowControl(
                    command_id=uuid5(NAMESPACE_URL, f"host:{invocation.id}:{operation_id}"),
                    expected_revision=record.revision,
                    action="cancel",
                ),
            )
            return {"status": 200, "body": {}}
        raise ConflictError("Unknown host job operation")


def execution_bindings() -> ExecutionBindings:
    # Service modules structurally implement the ledger contracts. Their public
    # functions stage changes in the caller's transaction and never commit.
    tokens: TokenLedger = quota_service
    capacity: CapacityLedger = quota_service
    return ExecutionBindings(tokens=tokens, capacity=capacity, admission_open=is_open)


class ExecutionAssets:
    def __init__(self, storage_root: Path, admission: ResourceScopeAdmission) -> None:
        self.storage_root = storage_root
        self.admission = admission

    def read_asset(
        self,
        session: Session,
        *,
        subject: ExecutionSubject,
        scope: ExecutionScope,
        ref: str,
        digest: str,
        destination: str,
    ) -> bytes:
        try:
            scheme, identifier, number = ref.split(":")
            if scheme != "file":
                raise ValueError
            file_id, version_number = UUID(identifier), int(number)
        except ValueError:
            raise ConflictError("An asset reference must identify file:UUID:version") from None
        version, payload = resource_files.read_execution_input(
            session,
            file_id=file_id,
            version_number=version_number,
            reader=ResourceReader(subject.user_id),
            scope=ScopeRef(
                configuration_id=scope.configuration_id,
            ),
            admission=self.admission,
            destination=destination,
            storage_root=self.storage_root,
        )
        if version.digest != digest:
            raise ConflictError("The immutable asset digest differs from the frozen Run")
        return payload

    def publish_output(
        self,
        session: Session,
        *,
        subject: ExecutionSubject,
        run_id: UUID,
        name: str,
        payload: bytes,
        media_type: str | None,
    ) -> UUID:
        run = session.get(Run, run_id)
        if run is None or run.principal_id != subject.user_id or run.status != "running":
            raise ForbiddenError("The Run may not publish this output")
        reader = ResourceReader(subject.user_id)

        def create() -> UUID:
            reservation = quota_service.reserve_storage(
                session,
                user_id=subject.user_id,
                amount=len(payload),
                idempotency_scope="orchestration.artifact",
                idempotency_key=f"{run_id}:{sha256(name.encode()).hexdigest()}",
            )
            artifact = artifacts.record_artifact(
                session,
                source_kind="run",
                source_id=run_id,
                owner_id=subject.user_id,
                name=name,
                content=payload,
                media_type=media_type,
                labels={},
                reader=reader,
                actor=audit_actor(run),
                authorization=RunOutputAuthorization(run_id, subject.user_id),
                storage_root=self.storage_root,
            )
            quota_service.settle_storage(
                session, reservation_id=reservation.id, actual_amount=len(payload)
            )
            return artifact.id

        return run_idempotent_create(
            session=session,
            scope_id=run.scope_id,
            actor_id=subject.user_id,
            operation=f"execution.output:{run_id}",
            idempotency_key=name,
            request_payload={"digest": sha256(payload).hexdigest(), "media_type": media_type},
            get_existing=lambda identifier: (
                artifacts.get_artifact(session, artifact_id=identifier, reader=reader).id
            ),
            create=create,
            resource_id=lambda identifier: identifier,
        )


class RunOutputAuthorization:
    def __init__(self, run_id: UUID, principal_id: UUID) -> None:
        self.run_id, self.principal_id = run_id, principal_id

    def authorize_record(
        self, *, reader: ResourceReader, source_kind: str, source_id: UUID
    ) -> None:
        if source_kind != "run" or source_id != self.run_id or reader.user_id != self.principal_id:
            raise ForbiddenError("The output producer does not own this Run")


class ExecutionModelConnections:
    def __init__(self, cipher: SecretCipher, context_windows: dict[str, int]) -> None:
        self.cipher = cipher
        self.context_windows = dict(context_windows)

    def resolve(
        self,
        session: Session,
        *,
        subject: ExecutionSubject,
        scope: ExecutionScope,
        connection_id: UUID,
        model: str,
    ) -> ModelConnection:
        actor = users.load_identity(session, subject.user_id)
        if actor is None or actor.status != "active":
            raise ForbiddenError("The execution principal is no longer active")
        connection = resolve_connection(
            session, actor=actor, connection_id=connection_id, model=model
        )
        context_tokens = self.context_windows.get(model)
        if context_tokens is None or context_tokens <= 0:
            raise ConflictError("The platform has not verified this model's context bound")
        secret = resolve_model_secret(
            session, cipher=self.cipher, actor=actor, connection_id=connection_id, model=model
        )
        key = secret.get("api_key")
        if not isinstance(key, str) or not key:
            raise ConflictError("The model credential has no API key")
        return ModelConnection(
            api_key=key,
            base_url=connection.base_url or "https://api.openai.com/v1",
            context_tokens=context_tokens,
        )


class NativeSessionWriter:
    def authorize_writer(
        self,
        session: Session,
        *,
        session_id: UUID,
        run_id: UUID,
        principal_id: UUID,
    ) -> None:
        run = session.get(Run, run_id)
        if run is None or run.session_id != session_id or run.principal_id != principal_id:
            raise ForbiddenError("The Run does not own this model Session")
        if run.status not in {"running", "finalizing", "waiting_approval"}:
            raise ConflictError("The Run may no longer write native history")
        record = session.get(SessionRecord, session_id)
        if record is None or record.status not in {"open", "closing"}:
            raise ConflictError("The native model Session is blocked")


class ExecutionApprovalNotifier:
    def notify_decided(
        self,
        session: Session,
        *,
        approval_id: UUID,
        decision: str,
        target: ApprovalTarget,
        decided_by_user_id: UUID,
        reason: str | None,
    ) -> None:
        if target.target_type != "orchestration.run":
            if target.target_type in {"flow_step", "flow_plan"}:
                from opsmesh.orchestration.runs.models import FlowRun

                flow = session.get(FlowRun, UUID(target.target_id.split(":", 1)[0]))
                if flow is None:
                    raise ConflictError("The approval target flow no longer exists")
                stage_signal(
                    session,
                    dispatch_id=uuid4(),
                    workflow_id=f"flow:{flow.id}:{flow.workflow_generation}",
                    signal_name="wake",
                    signal_args=(),
                )
            return
        run_id = UUID(target.target_id.split(":", 1)[0])
        if session.get(Run, run_id) is None:
            raise ConflictError("The approval target Run no longer exists")
        stage_signal(
            session,
            dispatch_id=uuid4(),
            workflow_id=f"run:{run_id}",
            signal_name="approval",
            signal_args=(str(approval_id),),
        )


def wake_user_runs(session: Session, *, user_id: UUID) -> None:
    for run_id in session.scalars(
        select(Run.id).where(Run.principal_id == user_id, Run.status == "queued").order_by(Run.id)
    ):
        stage_signal(
            session,
            dispatch_id=uuid4(),
            workflow_id=f"run:{run_id}",
            signal_name="wake",
            signal_args=(),
        )


class ConfigurationRunSources:
    def __init__(
        self, cipher: SecretCipher, admission: ScopeAdmission, scopes: ScopeAuthorization
    ) -> None:
        self.cipher, self.admission, self.scopes = cipher, admission, scopes

    def authorize_submission(
        self, session: Session, *, principal_id: UUID, configuration: dict[str, Any]
    ) -> None:
        if configuration.get("configuration_source") is not None:
            raise ForbiddenError("Configuration sources must be submitted by their owner")
        try:
            proof = current_call()
        except AuthenticationError:
            if configuration.get("parent_invocation_id") is not None:
                raise ForbiddenError(
                    "A parent invocation requires authenticated delegation"
                ) from None
            return
        parent = session.get(Invocation, proof.invocation_id)
        if (
            proof.operation != "orchestration.runs.create"
            or parent is None
            or parent.principal_id != principal_id
            or configuration.get("parent_invocation_id") != str(parent.id)
        ):
            raise ForbiddenError("The Run parent differs from its authenticated delegation")

    def authorize(self, session: Session, *, run_id: UUID) -> None:
        self._authorize(session, run_id=run_id, visited=set())

    def _authorize(self, session: Session, *, run_id: UUID, visited: set[UUID]) -> None:
        if run_id in visited or len(visited) >= 64:
            raise ForbiddenError("The delegated source chain is cyclic or too deep")
        visited.add(run_id)
        run = session.get(Run, run_id)
        actor = load_identity(session, run.principal_id) if run else None
        if run is None or actor is None or actor.status != "active":
            raise ForbiddenError("The source principal is unavailable")
        request = ExecutionRequest.model_validate(
            self.cipher.decrypt_payload(run.request_ciphertext)
        )
        if request.parent_invocation_id:
            parent = session.get(Invocation, request.parent_invocation_id)
            instance = session.get(ExtensionInstance, parent.instance_id) if parent else None
            if parent is None or instance is None or parent.principal_id != run.principal_id:
                raise ForbiddenError("The delegated Run parent is unavailable")
            instance = authorize_instance(
                session,
                instance.id,
                user=actor,
                scopes=self.scopes,
                execution=True,
            )
            installation = load_installation(session, parent.installation_id)
            authorize_installation(
                session, installation, user=installation_viewer(session, instance)
            )
            permissions = parent.snapshot.get("permissions", [])
            if (
                instance.status != "enabled"
                or installation.status != "enabled"
                or instance.revision != parent.instance_revision
                or instance.installation_id != installation.id
                or not isinstance(permissions, list)
                or not set(permissions)
                <= (set(instance.permissions) & set(installation.permission_ceiling))
            ):
                raise ForbiddenError("The delegated Run authorization changed")
            self.admission.admit(
                session,
                subject=ExecutionSubject(actor.user_id),
                scope=ExecutionScope(configuration_id=instance.configuration_id),
            )
            expected_owner = (
                f"configuration:{instance.configuration_id}"
                if instance.configuration_id
                else f"user:{actor.user_id}"
            )
            if (
                run.scope_id != (instance.configuration_id or actor.user_id)
                or run.owner_ref != expected_owner
            ):
                raise ForbiddenError("The delegated Run changed its parent's scope")
            source_run = parent.snapshot.get("run_id")
            if source_run:
                self._authorize(session, run_id=UUID(str(source_run)), visited=visited)
            if request.configuration_source is None:
                return
        authorize_run_configuration(
            session,
            actor=actor,
            run=run,
            request=request,
            admission=self.admission,
            delegated_parent_verified=request.parent_invocation_id is not None,
            cipher=self.cipher,
        )


class ConfigurationDirectories:
    def ensure_directory(
        self,
        session: Session,
        *,
        owner_user_id: UUID,
        configuration_id: UUID,
        directory_id: UUID | None,
    ) -> UUID:
        if directory_id is not None:
            self.authorize_directory(
                session,
                owner_user_id=owner_user_id,
                configuration_id=configuration_id,
                directory_id=directory_id,
            )
            return directory_id
        profile = session.get(UserProfile, owner_user_id)
        if profile is None or profile.status != "active":
            raise ForbiddenError("The directory owner is unavailable")
        path = validate_relative_path(f"{profile.work_root or 'work'}/{configuration_id}")
        existing = session.scalar(
            select(ResourceDirectory).where(
                ResourceDirectory.owner_user_id == owner_user_id,
                ResourceDirectory.path == path,
            )
        )
        if existing is not None:
            return existing.id
        directory = files.create_directory(
            session,
            owner_id=owner_user_id,
            path=path,
            admission=RejectingScopeAdmission(),
            reader=ResourceReader(owner_user_id),
            scope=ScopeRef(),
        )
        return directory.id

    def authorize_directory(
        self,
        session: Session,
        *,
        owner_user_id: UUID,
        configuration_id: UUID,
        directory_id: UUID,
    ) -> None:
        directory = session.get(ResourceDirectory, directory_id)
        if directory is None or directory.owner_user_id != owner_user_id:
            raise ForbiddenError("The configuration directory is unavailable to its owner")


class ConfigurationCleanupAuthorization:
    def admit_cleanup(
        self,
        session: Session,
        *,
        cleanup_id: UUID,
        subject: ExecutionSubject,
        scope: ExecutionScope,
    ) -> None:
        closure = session.get(ConfigurationClosure, cleanup_id)
        configuration = (
            session.get(UseConfiguration, scope.configuration_id)
            if scope.configuration_id
            else None
        )
        if (
            closure is None
            or configuration is None
            or closure.configuration_id != configuration.id
            or configuration.status not in {"draining", "closed"}
        ):
            raise ForbiddenError("The cleanup proof does not authorize this configuration")
        # Recorded closure authority can cancel each participant's scoped work;
        # it never grants the configuration owner access to their private history.


class ConfigurationDrain:
    def __init__(self, cipher: SecretCipher, bindings: ExecutionBindings) -> None:
        self.cipher, self.bindings = cipher, bindings

    def drain(
        self,
        session: Session,
        *,
        configuration_id: UUID,
        operation_id: UUID,
    ) -> tuple[int, int]:
        configuration = session.get(UseConfiguration, configuration_id)
        actor = load_identity(session, configuration.owner_user_id) if configuration else None
        if configuration is None or actor is None or self.bindings.cleanup is None:
            raise ForbiddenError("The configuration cleanup owner is unavailable")
        self.bindings.cleanup.admit_cleanup(
            session,
            cleanup_id=operation_id,
            subject=ExecutionSubject(actor.user_id),
            scope=ExecutionScope(configuration_id=configuration_id),
        )
        from opsmesh.orchestration.triggers.service import disable_configuration_triggers

        disable_configuration_triggers(session, configuration_id=configuration_id)
        pending, uncertain = drain_extensions(
            session,
            configuration_id=configuration_id,
            actor=actor,
        )
        child_pending, child_uncertain = drain_execution(
            session,
            configuration_id=configuration_id,
            cleanup_id=operation_id,
            authorization=self.bindings.cleanup,
            cipher=self.cipher,
        )
        return pending + child_pending, uncertain + child_uncertain


class ConfigurationOperations:
    """Expose the configuration owner to runs through its explicit injected contract."""

    def authorize_scope(
        self,
        session: Session,
        *,
        actor: CurrentUser,
        configuration_id: UUID,
        write: bool,
        manage: bool,
        allow_archived: bool,
    ) -> None:
        from opsmesh.orchestration.configurations.service import authorize_configuration

        if allow_archived:
            configuration = session.get(UseConfiguration, configuration_id)
            closure = session.scalar(
                select(ConfigurationClosure.id).where(
                    ConfigurationClosure.configuration_id == configuration_id
                )
            )
            if configuration and configuration.status in {"draining", "closed"} and closure:
                return
        authorize_configuration(
            session,
            actor=actor,
            configuration_id=configuration_id,
            permission="manage" if manage else "use" if write else "read",
            allow_disabled=allow_archived,
        )

    def flow_definition(
        self,
        session: Session,
        *,
        actor: CurrentUser,
        configuration_id: UUID,
    ) -> tuple[CurrentUser, UUID]:
        from opsmesh.orchestration.configurations.service import (
            authorize_configuration,
            owner_identity,
        )

        configuration = authorize_configuration(
            session, actor=actor, configuration_id=configuration_id, permission="use"
        )
        return owner_identity(session, configuration), configuration.definition_revision_id

    def authorize_approver(
        self,
        session: Session,
        *,
        approver: CurrentUser,
        owner_id: UUID,
        configuration_id: UUID | None,
    ) -> None:
        """Authorize the assigned approver without granting run history access."""
        if approver.status != "active":
            raise ForbiddenError("The assigned approver is inactive")
        if configuration_id is None:
            if approver.user_id != owner_id:
                raise ForbiddenError("A private run can only be approved by its principal")
            return
        from opsmesh.orchestration.configurations.service import authorize_configuration

        authorize_configuration(
            session,
            actor=approver,
            configuration_id=configuration_id,
            permission="use",
        )

    def validate_agent(
        self,
        session: Session,
        *,
        actor: CurrentUser,
        revision_id: UUID,
        admission: ScopeAdmission,
    ) -> None:
        from opsmesh.orchestration.configurations.models import ConfigurationRevision
        from opsmesh.orchestration.configurations.service import assemble_configuration

        version = session.get(ConfigurationRevision, revision_id)
        if version is None:
            raise ForbiddenError("The Agent node configuration revision is unavailable")
        _, frozen = assemble_configuration(
            session,
            actor=actor,
            configuration_id=version.configuration_id,
            revision=version,
            session_id=UUID(int=0),
            input="",
            admission=admission,
        )
        if frozen != version.frozen_bindings:
            raise ForbiddenError("The Agent node configuration bindings changed")

    def submit_agent(
        self,
        session: Session,
        *,
        actor: CurrentUser,
        revision_id: UUID,
        input: Any,
        idempotency_key: str,
        initiated_by_id: UUID,
        admission: ScopeAdmission,
        cipher: SecretCipher,
        assets: RunAssets,
    ) -> UUID:
        from opsmesh.orchestration.configurations.models import ConfigurationRevision
        from opsmesh.orchestration.configurations.schemas import ConfigurationRunCreate
        from opsmesh.orchestration.configurations.service import submit_configuration_run

        version = session.get(ConfigurationRevision, revision_id)
        if version is None:
            raise ForbiddenError("The Agent node configuration revision is unavailable")
        agent = submit_configuration_run(
            session,
            actor=actor,
            configuration_id=version.configuration_id,
            request=ConfigurationRunCreate(configuration_revision=version.number, input=input),
            idempotency_key=idempotency_key,
            admission=admission,
            cipher=cipher,
            assets=assets,
        )
        agent.initiated_by_id = initiated_by_id
        return agent.id
