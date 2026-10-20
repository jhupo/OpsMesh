"""Configuration revisions and invitations without a tenant container."""

from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.identity.context import CurrentUser
from opsmesh.identity.users.models import UserProfile
from opsmesh.orchestration.configurations.compilation import compile_configuration
from opsmesh.orchestration.configurations.models import (
    ConfigurationInvitation,
    ConfigurationMember,
    ConfigurationRevision,
    ConfigurationRun,
    UseConfiguration,
)
from opsmesh.orchestration.configurations.schemas import (
    ConfigurationCreate,
    ConfigurationInvitationCreate,
    ConfigurationRunCreate,
    ConfigurationSpec,
    ConfigurationUpdate,
)
from opsmesh.orchestration.contracts import (
    ExecutionScope,
    ExecutionSubject,
    RunAssets,
    ScopeAdmission,
)
from opsmesh.orchestration.definitions.schemas import Permission
from opsmesh.orchestration.definitions.service import granted, load_revision
from opsmesh.orchestration.execution.contracts import (
    ExecutionRequest,
)
from opsmesh.orchestration.runs import service as runs
from opsmesh.orchestration.runs.models import Run
from opsmesh.orchestration.sessions import service as sessions
from opsmesh.orchestration.sessions.models import SESSION_OPEN, SessionRecord
from opsmesh.shared.db.idempotency import run_idempotent_create
from opsmesh.shared.errors import ConflictError, ForbiddenError, NotFoundError
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.shared.serialization import payload_digest


class ConfigurationResources(Protocol):
    def ensure_directory(
        self,
        session: Session,
        *,
        owner_user_id: UUID,
        configuration_id: UUID,
        directory_id: UUID | None,
    ) -> UUID: ...

    def authorize_directory(
        self,
        session: Session,
        *,
        owner_user_id: UUID,
        configuration_id: UUID,
        directory_id: UUID,
    ) -> None: ...


def owner_identity(session: Session, configuration: UseConfiguration) -> CurrentUser:
    owner = session.get(UserProfile, configuration.owner_user_id, populate_existing=True)
    if owner is None or owner.status != "active":
        raise ForbiddenError("The configuration owner is inactive")
    return CurrentUser(
        owner.id, owner.email, owner.display_name, owner.status, frozenset(owner.platform_roles)
    )


def authorize_configuration(
    session: Session,
    *,
    actor: CurrentUser,
    configuration_id: UUID,
    permission: Permission = "read",
    lock: bool = False,
    allow_disabled: bool = False,
) -> UseConfiguration:
    if actor.status != "active":
        raise ForbiddenError("The configuration participant is inactive")
    current = session.get(UserProfile, actor.user_id, populate_existing=True)
    if current is None or current.status != "active":
        raise ForbiddenError("The configuration participant is inactive")
    record = session.get(
        UseConfiguration, configuration_id, populate_existing=True, with_for_update=lock
    )
    if record is None:
        raise NotFoundError("Use configuration not found")
    if record.owner_user_id != actor.user_id:
        member = session.scalar(
            select(ConfigurationMember).where(
                ConfigurationMember.configuration_id == configuration_id,
                ConfigurationMember.user_id == actor.user_id,
            )
        )
        if member is None or not granted(member.permissions, permission):
            raise NotFoundError("Use configuration not found")
    owner_draft = record.status == "draft" and record.owner_user_id == actor.user_id
    if not allow_disabled and record.status != "active" and not owner_draft:
        raise ConflictError("The use configuration is disabled")
    if owner_draft and permission == "use":
        raise ConflictError("The use configuration has not been published")
    if not allow_disabled:
        load_revision(
            session,
            actor=owner_identity(session, record),
            revision_id=record.definition_revision_id,
            use=True,
        )
    return record


def authorize_configuration_session(
    session: Session,
    *,
    actor: CurrentUser,
    configuration_id: UUID,
    session_id: UUID,
    permission: Permission = "read",
) -> UseConfiguration:
    from opsmesh.orchestration.sessions.models import SessionRecord

    configuration = authorize_configuration(
        session, actor=actor, configuration_id=configuration_id, permission=permission
    )
    record = session.get(SessionRecord, session_id)
    if (
        record is None
        or record.configuration_id != configuration_id
        or record.scope_id != actor.user_id
        or record.owner_ref != f"user:{actor.user_id}"
        or record.principal_id != actor.user_id
    ):
        raise NotFoundError("Configuration Session not found")
    return configuration


def get_configuration_revision(
    session: Session,
    *,
    configuration_id: UUID,
    number: int,
) -> ConfigurationRevision:
    revision = session.scalar(
        select(ConfigurationRevision).where(
            ConfigurationRevision.configuration_id == configuration_id,
            ConfigurationRevision.number == number,
        )
    )
    if revision is None:
        raise NotFoundError("Configuration revision not found")
    return revision


def create_configuration(
    session: Session,
    *,
    actor: CurrentUser,
    request: ConfigurationCreate,
    resources: ConfigurationResources,
    idempotency_key: str,
    admission: ScopeAdmission,
) -> UseConfiguration:
    load_revision(session, actor=actor, revision_id=request.definition_revision_id, use=True)

    def create() -> UseConfiguration:
        identifier = uuid4()
        directory_id = resources.ensure_directory(
            session,
            owner_user_id=actor.user_id,
            configuration_id=identifier,
            directory_id=request.root_directory_id,
        )
        record = UseConfiguration(
            id=identifier,
            owner_user_id=actor.user_id,
            definition_revision_id=request.definition_revision_id,
            root_directory_id=directory_id,
            name=request.name,
            status="draft" if request.specification is None else "active",
            revision=0 if request.specification is None else 1,
        )
        session.add(record)
        session.flush()
        if request.specification is not None:
            append_configuration_revision(
                session,
                actor=actor,
                record=record,
                specification=request.specification,
                admission=admission,
            )
        return record

    return run_idempotent_create(
        session=session,
        scope_id=actor.user_id,
        actor_id=actor.user_id,
        operation="orchestration.configurations.create",
        idempotency_key=idempotency_key,
        request_payload=request.model_dump(mode="json"),
        get_existing=lambda identifier: authorize_configuration(
            session, actor=actor, configuration_id=identifier
        ),
        create=create,
        resource_id=lambda record: record.id,
    )


def append_configuration_revision(
    session: Session,
    *,
    actor: CurrentUser,
    record: UseConfiguration,
    specification: ConfigurationSpec,
    admission: ScopeAdmission,
) -> ConfigurationRevision:
    payload = specification.model_dump(mode="json")
    revision = ConfigurationRevision(
        configuration_id=record.id,
        number=record.revision,
        specification=payload,
        frozen_bindings={},
        digest=payload_digest(payload),
        created_by_id=actor.user_id,
    )
    session.add(revision)
    session.flush()
    _, definition = load_revision(
        session,
        actor=owner_identity(session, record),
        revision_id=record.definition_revision_id,
        use=True,
    )
    if definition.kind == "agent_graph":
        _, frozen = compile_configuration(
            session,
            actor=owner_identity(session, record),
            configuration=record,
            revision=revision,
            session_id=UUID(int=0),
            input="",
            admission=admission,
        )
        revision.frozen_bindings = frozen
        revision.digest = payload_digest({"specification": payload, "bindings": frozen})
    return revision


def update_configuration(
    session: Session,
    *,
    actor: CurrentUser,
    configuration_id: UUID,
    request: ConfigurationUpdate,
    admission: ScopeAdmission,
) -> ConfigurationRevision:
    record = authorize_configuration(
        session, actor=actor, configuration_id=configuration_id, permission="edit", lock=True
    )
    if record.revision != request.expected_revision:
        raise ConflictError("The configuration revision changed")
    # Changing shared connections is an owner decision, not an invitee permission.
    if record.owner_user_id != actor.user_id:
        raise ForbiddenError("Only the owner may authorize connection and resource mappings")
    record.revision += 1
    record.status = "active"
    return append_configuration_revision(
        session,
        actor=actor,
        record=record,
        specification=request.specification,
        admission=admission,
    )


def invite_member(
    session: Session,
    *,
    actor: CurrentUser,
    configuration_id: UUID,
    request: ConfigurationInvitationCreate,
) -> ConfigurationInvitation:
    configuration = authorize_configuration(
        session, actor=actor, configuration_id=configuration_id, permission="manage", lock=True
    )
    if "manage" in request.permissions and actor.user_id != configuration.owner_user_id:
        raise ForbiddenError("Only the owner can delegate configuration management")
    user = session.get(UserProfile, request.user_id)
    if user is None or user.status != "active":
        raise NotFoundError("The invited account is unavailable")
    record = ConfigurationInvitation(
        configuration_id=configuration_id,
        invited_user_id=request.user_id,
        invited_by_id=actor.user_id,
        permissions=list(dict.fromkeys(request.permissions)),
        directory_access=request.directory_access,
        expires_at=datetime.now(UTC) + timedelta(seconds=request.expires_in_seconds),
    )
    session.add(record)
    session.flush()
    return record


def accept_invitation(
    session: Session,
    *,
    actor: CurrentUser,
    invitation_id: UUID,
) -> ConfigurationMember:
    invitation = session.get(ConfigurationInvitation, invitation_id, with_for_update=True)
    if invitation is None or invitation.invited_user_id != actor.user_id:
        raise NotFoundError("Invitation not found")
    if actor.status != "active" or datetime.now(UTC) >= invitation.expires_at:
        raise ForbiddenError("The invitation expired or the account is inactive")
    if invitation.status not in {"pending", "accepted"}:
        raise ConflictError("The invitation is no longer pending")
    inviter = session.get(UserProfile, invitation.invited_by_id)
    if inviter is None:
        raise ForbiddenError("The inviter is unavailable")
    authorize_configuration(
        session,
        actor=CurrentUser(
            inviter.id,
            inviter.email,
            inviter.display_name,
            inviter.status,
            frozenset(inviter.platform_roles),
        ),
        configuration_id=invitation.configuration_id,
        permission="manage",
        lock=True,
    )
    member = session.scalar(
        select(ConfigurationMember).where(
            ConfigurationMember.configuration_id == invitation.configuration_id,
            ConfigurationMember.user_id == actor.user_id,
        )
    )
    if invitation.status == "accepted":
        if member is None:
            raise ConflictError("The accepted invitation was subsequently revoked")
        return member
    if member is None:
        member = ConfigurationMember(
            configuration_id=invitation.configuration_id, user_id=actor.user_id
        )
        session.add(member)
    member.permissions = invitation.permissions
    member.directory_access = invitation.directory_access
    invitation.status = "accepted"
    session.flush()
    return member


def assemble_configuration(
    session: Session,
    *,
    actor: CurrentUser,
    configuration_id: UUID,
    revision: ConfigurationRevision,
    session_id: UUID,
    input: Any,
    admission: ScopeAdmission,
) -> tuple[ExecutionRequest, dict[str, Any]]:
    configuration = authorize_configuration(
        session, actor=actor, configuration_id=configuration_id, permission="use"
    )
    return compile_configuration(
        session,
        actor=owner_identity(session, configuration),
        configuration=configuration,
        revision=revision,
        session_id=session_id,
        input=input,
        admission=admission,
    )


def default_configuration_session(
    session: Session,
    *,
    actor: CurrentUser,
    configuration_id: UUID,
    admission: ScopeAdmission,
) -> SessionRecord:
    """Serialize allocation on configuration so each user has one active default Session."""
    authorize_configuration(
        session, actor=actor, configuration_id=configuration_id, permission="use", lock=True
    )
    record = session.scalar(
        select(SessionRecord)
        .where(
            SessionRecord.configuration_id == configuration_id,
            SessionRecord.scope_id == actor.user_id,
            SessionRecord.owner_ref == f"user:{actor.user_id}",
            SessionRecord.principal_id == actor.user_id,
            SessionRecord.status == SESSION_OPEN,
        )
        .order_by(SessionRecord.created_at, SessionRecord.id)
        .limit(1)
    )
    return record or sessions.create_session(
        session,
        subject=ExecutionSubject(actor.user_id),
        scope=ExecutionScope(configuration_id=configuration_id),
        admission=admission,
    )


def submit_configuration_run(
    session: Session,
    *,
    actor: CurrentUser,
    configuration_id: UUID,
    request: ConfigurationRunCreate,
    idempotency_key: str,
    admission: ScopeAdmission,
    cipher: SecretCipher,
    assets: RunAssets,
) -> Run:
    authorize_configuration(
        session, actor=actor, configuration_id=configuration_id, permission="use", lock=True
    )
    if request.session_id:
        authorize_configuration_session(
            session,
            actor=actor,
            configuration_id=configuration_id,
            session_id=request.session_id,
            permission="use",
        )

    def existing(identifier: UUID) -> Run:
        record = session.get(Run, identifier)
        if (
            record is None
            or record.owner_ref != f"configuration:{configuration_id}"
            or record.principal_id != actor.user_id
        ):
            raise ForbiddenError("The execution is unavailable")
        return record

    def create() -> Run:
        version = get_configuration_revision(
            session, configuration_id=configuration_id, number=request.configuration_revision
        )
        subject = ExecutionSubject(actor.user_id)
        scope = ExecutionScope(configuration_id=configuration_id)
        session_id = (
            request.session_id
            or default_configuration_session(
                session, actor=actor, configuration_id=configuration_id, admission=admission
            ).id
        )
        assembled, frozen = assemble_configuration(
            session,
            actor=actor,
            configuration_id=configuration_id,
            revision=version,
            session_id=session_id,
            input=request.input,
            admission=admission,
        )
        if frozen != version.frozen_bindings:
            raise ForbiddenError(
                "A referenced binding changed; publish a new configuration revision"
            )
        for asset in assembled.assets:
            assets.read_asset(
                session,
                subject=subject,
                scope=scope,
                ref=asset.ref,
                digest=asset.digest,
                destination=asset.destination,
            )
        assembled = assembled.model_copy(update={"configuration_source": str(version.id)})
        run = runs.execute(
            session,
            subject=subject,
            admission=admission,
            cipher=cipher,
            request=assembled,
            idempotency_key=f"configuration:{configuration_id}:{actor.user_id}:{idempotency_key}",
        )
        run.initiated_by_id = actor.user_id
        session.add(
            ConfigurationRun(
                configuration_id=configuration_id,
                revision_id=version.id,
                run_id=run.id,
                fingerprint=payload_digest(assembled.model_dump(mode="json")),
            )
        )
        session.flush()
        return run

    return run_idempotent_create(
        session=session,
        scope_id=configuration_id,
        actor_id=actor.user_id,
        operation="orchestration.configurations.run",
        idempotency_key=idempotency_key,
        request_payload=request.model_dump(mode="json"),
        get_existing=existing,
        create=create,
        resource_id=lambda record: record.id,
    )


def authorize_run_configuration(
    session: Session,
    *,
    actor: CurrentUser,
    run: Run,
    request: ExecutionRequest,
    admission: ScopeAdmission,
    delegated_parent_verified: bool = False,
    cipher: SecretCipher | None = None,
) -> None:
    if request.configuration_source is None:
        if run.owner_ref.startswith("configuration:") and not (
            delegated_parent_verified and request.parent_invocation_id is not None
        ):
            raise ForbiddenError("The configuration Run has no trusted source")
        return
    link = session.scalar(select(ConfigurationRun).where(ConfigurationRun.run_id == run.id))
    version = session.get(ConfigurationRevision, UUID(request.configuration_source))
    if link is None or version is None or link.revision_id != version.id:
        raise ForbiddenError("The Run configuration provenance is unavailable")
    initiator = session.get(UserProfile, run.initiated_by_id)
    if initiator is None:
        raise ForbiddenError("The initiating account was deleted")
    actual_actor = CurrentUser(
        initiator.id,
        initiator.email,
        initiator.display_name,
        initiator.status,
        frozenset(initiator.platform_roles),
    )
    from opsmesh.orchestration.runs.models import FlowRun, StepAttempt

    child_step = session.scalar(
        select(StepAttempt).where(StepAttempt.child_kind == "agent", StepAttempt.child_id == run.id)
    )
    assembly_actor = actual_actor
    if child_step is not None:
        if cipher is None:
            raise ForbiddenError("Flow child provenance decryption is not assembled")
        parent = session.get(FlowRun, child_step.run_id)
        node = cipher.decrypt_payload(child_step.node_ciphertext)
        if (
            parent is None
            or parent.initiated_by_id != run.initiated_by_id
            or (
                parent.principal_id != run.principal_id
                or node.get("configuration_revision_id") != str(version.id)
            )
        ):
            raise ForbiddenError("The Flow does not own this fixed Agent configuration")
        if parent.configuration_id is not None:
            authorize_configuration(
                session,
                actor=actual_actor,
                configuration_id=parent.configuration_id,
                permission="use",
            )
        elif actual_actor.user_id != parent.principal_id:
            raise ForbiddenError("The Flow parent principal changed")
        authorize_configuration(
            session, actor=actual_actor, configuration_id=link.configuration_id, permission="use"
        )
    authorize_configuration_session(
        session,
        actor=assembly_actor,
        configuration_id=link.configuration_id,
        session_id=run.session_id,
        permission="use",
    )
    assembled, frozen = assemble_configuration(
        session,
        actor=assembly_actor,
        configuration_id=link.configuration_id,
        revision=version,
        session_id=run.session_id,
        input=request.input,
        admission=admission,
    )
    assembled = assembled.model_copy(
        update={
            "configuration_source": str(version.id),
            "parent_invocation_id": request.parent_invocation_id,
        }
    )
    if (
        run.principal_id != actor.user_id
        or frozen != version.frozen_bindings
        or (payload_digest(assembled.model_dump(mode="json")) != link.fingerprint)
    ):
        raise ForbiddenError("The immutable configuration is no longer authorized")
