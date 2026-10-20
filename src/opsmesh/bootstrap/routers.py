"""Register feature routers and assemble the governance and delegation identities.

Governance modules never import identity: this assembly point replaces their
placeholder dependencies with the real identity channels, and resolves the
restricted delegation subject that extension-reserved operations require.
Resource, audit and execution admissions delegate to the configuration's
current object grants while retaining the actual caller's identity.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.capabilities.routes import router as capability_router
from opsmesh.channels import routes as channel_routes
from opsmesh.extensions.invocation import load_invocation
from opsmesh.extensions.routes import router as extension_router
from opsmesh.identity.access import require_platform_admin
from opsmesh.identity.auth.dependencies import (
    INSTALLATION_HEADER,
    INSTANCE_HEADER,
    INVOCATION_HEADER,
    NAMESPACE_HEADER,
    CurrentUserDep,
    DelegatedUserDep,
)
from opsmesh.identity.auth.routes import router as auth_router
from opsmesh.identity.context import CurrentUser
from opsmesh.identity.errors import AuthenticationError
from opsmesh.identity.invitations.routes import router as invitation_router
from opsmesh.identity.quotas.routes import router as quota_router
from opsmesh.identity.users.routes import router as user_router
from opsmesh.identity.users.service import load_identity
from opsmesh.orchestration.configurations.bindings.routes import router as binding_router
from opsmesh.orchestration.contracts import ExecutionScope, ExecutionSubject
from opsmesh.orchestration.runs import routes as run_routes
from opsmesh.orchestration.runs.model_routes import router as model_operations_router
from opsmesh.orchestration.runs.models import RUN_ACTIVE_STATUSES, Run
from opsmesh.orchestration.runtime import mcp_routes as runtime_mcp_routes
from opsmesh.orchestration.runtime import routes as runtime_routes
from opsmesh.orchestration.sessions import routes as session_routes
from opsmesh.platform.approvals import routes as approval_routes
from opsmesh.platform.audit import routes as audit_routes
from opsmesh.platform.health import routes as health_routes
from opsmesh.platform.model_connections import routes as model_provider_routes
from opsmesh.platform.operations import routes as operations_routes
from opsmesh.platform.policies import routes as policy_routes
from opsmesh.resources.artifacts import routes as artifact_routes
from opsmesh.resources.files import routes as file_routes
from opsmesh.resources.files.access import ResourceReader, ScopeRef
from opsmesh.shared.audit import AuditActor
from opsmesh.shared.errors import ForbiddenError
from opsmesh.shared.http.dependencies import get_db_session


def _audit_reader(user: CurrentUserDep) -> CurrentUser:
    """Audit reads are authorized per scope by the audit routes themselves."""
    return user


class ConfigurationScopeAdmission:
    def admit(self, session: Session, *, reader: ResourceReader, scope: ScopeRef) -> None:
        if scope.configuration_id is None:
            return
        from opsmesh.orchestration.configurations.service import authorize_configuration

        actor = load_identity(session, reader.user_id)
        if actor is None or actor.status != "active":
            raise ForbiddenError("The configuration reader is unavailable")
        authorize_configuration(
            session, actor=actor, configuration_id=scope.configuration_id, permission="read"
        )


class ConfigurationAuditScopeAdmission:
    def admit(self, session: Session, *, user_id: UUID, configuration_id: UUID | None) -> None:
        if configuration_id is None:
            from opsmesh.extensions.delegation import current_call
            from opsmesh.platform.audit.models import AuditEvent

            try:
                call = current_call()
            except AuthenticationError:
                raise ForbiddenError("A configuration audit scope is required") from None
            # The verified path may use a custom API prefix.
            parts = call.path.rsplit("/platform/audit/", 1)
            if call.operation != "audit.read" or call.method != "GET" or len(parts) != 2:
                raise ForbiddenError("The delegation does not admit this audit read")
            try:
                event_id = UUID(parts[1])
            except ValueError:
                raise ForbiddenError("The delegation requires a specific audit record") from None
            invocation = load_invocation(session, call.invocation_id)
            event = session.get(AuditEvent, event_id)
            if (
                invocation.principal_id != user_id
                or invocation.snapshot.get("configuration_id")
                or event is None
                or event.configuration_id is not None
                or event.instance_id != invocation.instance_id
                or event.actor_id != str(user_id)
            ):
                raise ForbiddenError("The audit record is outside the delegated scope")
            return
        ConfigurationScopeAdmission().admit(
            session, reader=ResourceReader(user_id), scope=ScopeRef(configuration_id)
        )


class ConfigurationExecutionScopeAdmission:
    def authorize_model(
        self,
        session: Session,
        *,
        user_id: UUID,
        connection_id: UUID,
        model: str,
    ) -> int:
        from opsmesh.platform.model_connections.service import resolve_connection

        actor = load_identity(session, user_id)
        if actor is None or actor.status != "active":
            raise ForbiddenError("The model user is unavailable")
        return resolve_connection(
            session, actor=actor, connection_id=connection_id, model=model
        ).revision

    def admit_resource_use(
        self,
        session: Session,
        *,
        subject: ExecutionSubject,
        scope: ExecutionScope,
        kind: str,
        resource_id: UUID,
        owner_user_id: UUID,
    ) -> None:
        from opsmesh.extensions.models import ExtensionInstance
        from opsmesh.orchestration.configurations.bindings.models import CapabilityBinding
        from opsmesh.orchestration.configurations.schemas import ConfigurationSpec
        from opsmesh.orchestration.configurations.service import (
            authorize_configuration,
            get_configuration_revision,
        )
        from opsmesh.orchestration.runtime.models import MCPConnection

        actor = load_identity(session, subject.user_id)
        if actor is None or scope.configuration_id is None:
            raise ForbiddenError("The shared runtime resource is unavailable")
        configuration = authorize_configuration(
            session, actor=actor, configuration_id=scope.configuration_id, permission="use"
        )
        if configuration.owner_user_id != owner_user_id:
            raise ForbiddenError("The runtime resource belongs to another configuration owner")
        revision = get_configuration_revision(
            session, configuration_id=configuration.id, number=configuration.revision
        )
        settings = ConfigurationSpec.model_validate(revision.specification)
        if kind == "environment" and settings.environment_id == resource_id:
            return
        binding_ids = [item.binding_id for item in settings.capabilities.values()]
        binding_ids.extend(item.binding_id for item in settings.hooks)
        for binding_id in binding_ids:
            binding = session.get(CapabilityBinding, binding_id)
            if (
                binding is None
                or binding.owner_user_id != owner_user_id
                or binding.status != "enabled"
            ):
                continue
            if kind == "mcp" and binding.configuration.get("mcp_connection_id") == str(resource_id):
                return
            mcp_id = binding.configuration.get("mcp_connection_id")
            mcp = session.get(MCPConnection, UUID(str(mcp_id))) if mcp_id else None
            if (
                kind == "environment"
                and mcp is not None
                and mcp.principal_id == owner_user_id
                and mcp.scope_id == configuration.id
                and mcp.owner_ref == f"configuration:{configuration.id}"
                and mcp.status == "ready"
                and mcp.environment_id == resource_id
            ):
                return
            identifier = binding.configuration.get("instance_id")
            instance = session.get(ExtensionInstance, UUID(str(identifier))) if identifier else None
            if (
                kind == "environment"
                and instance is not None
                and instance.owner_id == owner_user_id
                and instance.configuration_id == configuration.id
                and instance.status == "enabled"
                and instance.environment_id == resource_id
            ):
                return
        raise ForbiddenError("The configuration does not grant this runtime resource")

    def admit(self, session: Session, *, subject: ExecutionSubject, scope: ExecutionScope) -> None:
        actor = load_identity(session, subject.user_id)
        if actor is None or actor.status != "active":
            raise ForbiddenError("The execution principal is unavailable")
        if scope.configuration_id is None:
            return
        from opsmesh.orchestration.configurations.models import UseConfiguration
        from opsmesh.orchestration.configurations.service import authorize_configuration

        configuration = session.get(UseConfiguration, scope.configuration_id)
        if (
            configuration is not None
            and configuration.status == "draft"
            and configuration.owner_user_id == actor.user_id
        ):
            authorize_configuration(
                session, actor=actor, configuration_id=configuration.id, permission="edit"
            )
            return

        authorize_configuration(
            session, actor=actor, configuration_id=scope.configuration_id, permission="use"
        )


class ActiveRunActivity:
    """The real run guard: a session with a non-terminal run is active.

    The run table belongs to execution.runs, which the session modules
    never import; this assembly point answers their guard from it. A
    needs-attention run still counts as active, because its outcome is
    unknown and it may yet write the session's history.
    """

    def has_active_runs(self, session: Session, *, session_id: UUID) -> bool:
        statement = (
            select(Run.id)
            .where(Run.session_id == session_id, Run.status.in_(RUN_ACTIVE_STATUSES))
            .limit(1)
        )
        return session.scalar(statement) is not None

    def has_retained_runs(self, session: Session, *, session_id: UUID) -> bool:
        return (
            session.scalar(select(Run.id).where(Run.session_id == session_id).limit(1)) is not None
        )


def _extension_audit_actor(
    user: DelegatedUserDep,
    session: Annotated[Session, Depends(get_db_session)],
    x_installation_id: Annotated[str | None, Header(alias=INSTALLATION_HEADER)],
    x_instance_id: Annotated[str | None, Header(alias=INSTANCE_HEADER)],
    x_invocation_id: Annotated[str | None, Header(alias=INVOCATION_HEADER)],
    x_namespace: Annotated[str | None, Header(alias=NAMESPACE_HEADER)],
) -> AuditActor:
    """The verified delegation is the only actor an extension append may claim."""
    invocation = load_invocation(session, _uuid(x_invocation_id, INVOCATION_HEADER))
    return AuditActor(
        actor_type="delegation",
        actor_id=str(user.user_id),
        installation_id=_uuid(x_installation_id, INSTALLATION_HEADER),
        instance_id=_uuid(x_instance_id, INSTANCE_HEADER),
        invocation_id=_uuid(x_invocation_id, INVOCATION_HEADER),
        package_key=x_namespace,
        configuration_id=UUID(str(invocation.snapshot["configuration_id"]))
        if invocation.snapshot.get("configuration_id")
        else None,
        configuration_revision=str(invocation.instance_revision),
    )


def _approval_subject(user: CurrentUserDep) -> CurrentUser:
    return user


def _security_reader(user: CurrentUserDep) -> CurrentUser:
    require_platform_admin(user)
    return user


def _resource_reader(user: CurrentUserDep) -> ResourceReader:
    """The verified session identity is the only resource reader."""
    return ResourceReader(user_id=user.user_id)


def _uuid(value: str | None, header: str) -> UUID:
    if value is None:
        raise AuthenticationError(f"The {header} header is required")
    try:
        return UUID(value)
    except ValueError as exc:
        raise AuthenticationError(f"The {header} header is malformed") from exc


def register_routers(app: FastAPI, *, prefix: str) -> None:
    """Include feature routers under the configured API prefix."""
    from opsmesh.capabilities.publication_routes import router as capability_publication_router
    from opsmesh.orchestration.configurations.routes import router as configuration_router
    from opsmesh.orchestration.definitions.routes import router as flow_definitions_router
    from opsmesh.orchestration.triggers.routes import router as flow_triggers_router

    for router in (
        configuration_router,
        flow_definitions_router,
        flow_triggers_router,
        auth_router,
        user_router,
        quota_router,
        invitation_router,
        audit_routes.router,
        approval_routes.router,
        policy_routes.router,
        health_routes.router,
        operations_routes.router,
        capability_router,
        capability_publication_router,
        session_routes.router,
        run_routes.router,
        model_operations_router,
        runtime_routes.router,
        runtime_mcp_routes.router,
        extension_router,
        file_routes.router,
        artifact_routes.router,
        binding_router,
        model_provider_routes.router,
        channel_routes.router,
    ):
        app.include_router(router, prefix=prefix)
    app.dependency_overrides[audit_routes.require_audit_reader] = _audit_reader
    app.dependency_overrides[audit_routes.require_extension_actor] = _extension_audit_actor
    app.dependency_overrides[approval_routes.require_approval_subject] = _approval_subject
    app.dependency_overrides[file_routes.require_resource_reader] = _resource_reader
    app.dependency_overrides[artifact_routes.require_resource_reader] = _resource_reader
