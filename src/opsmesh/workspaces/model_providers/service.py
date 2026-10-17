"""Model provider service: connection lifecycle and default model resolution.

Connections reference credentials owned by the credentials module; the key
itself is never read here. A probe is staged as a durable workflow
execution in the same transaction that marks the connection as probing,
so a committed start always has its delivery intent. Base URLs are
checked against the HTTPS egress policy before they are stored.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from opsmesh.governance.audit.service import AuditActor, append_platform_event
from opsmesh.identity.context import CurrentUser
from opsmesh.shared.db.pagination import page_scalars
from opsmesh.shared.errors import ConflictError, InvalidArgumentError, NotFoundError
from opsmesh.shared.pagination import PageParams
from opsmesh.shared.security.egress import (
    EgressURLPolicy,
    EgressURLValidationError,
    validate_egress_url,
)
from opsmesh.temporal.dispatch.outbox import stage_execution
from opsmesh.workspaces.credentials.models import (
    CREDENTIAL_KIND_MODEL_PROVIDER,
    CREDENTIAL_STATUS_ACTIVE,
    WorkspaceCredential,
)
from opsmesh.workspaces.members import access
from opsmesh.workspaces.model_providers.models import (
    CONNECTION_STATUS_ACTIVE,
    CONNECTION_STATUS_DISABLED,
    MAX_MODELS,
    PROBE_FAILED,
    PROBE_NEVER,
    PROBE_RUNNING,
    PROVIDER_OPENAI,
    ModelProviderConnection,
)

MODEL_PROVIDER_PROBE_WORKFLOW = "ModelProviderProbeWorkflow"
BASE_URL_POLICY = EgressURLPolicy(allowed_schemes=frozenset({"https"}))
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"


class ModelProviderProbeExecution(BaseModel):
    """The durable command that probes one connection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_id: UUID
    workflow_id: str = Field(min_length=1, max_length=255)
    workflow_name: str = MODEL_PROVIDER_PROBE_WORKFLOW
    connection_id: UUID


def create_connection(
    session: Session,
    *,
    actor: CurrentUser,
    workspace_id: UUID,
    name: str,
    credential_id: UUID,
    base_url: str | None,
    default_model: str,
    models: list[str],
    is_default: bool,
) -> ModelProviderConnection:
    """Register one connection over an existing active provider credential."""
    access.require_workspace_member(
        session, workspace_id=workspace_id, user_id=actor.user_id, permission="manage", lock=True
    )
    _require_provider_credential(session, workspace_id=workspace_id, credential_id=credential_id)
    validated_base_url = _validated_base_url(base_url)
    validated_models = _validated_models(models)
    if default_model not in validated_models:
        raise InvalidArgumentError(
            "The default model must be one of the declared models",
            details={"default_model": default_model},
        )
    if is_default:
        _unset_other_defaults(session, workspace_id=workspace_id)
    connection = ModelProviderConnection(
        workspace_id=workspace_id,
        credential_id=credential_id,
        name=name,
        provider=PROVIDER_OPENAI,
        base_url=validated_base_url,
        default_model=default_model,
        models=validated_models,
        is_default=is_default,
        status=CONNECTION_STATUS_ACTIVE,
        last_probe_status=PROBE_NEVER,
        created_by_user_id=actor.user_id,
    )
    session.add(connection)
    try:
        session.flush()
    except IntegrityError as exc:
        raise ConflictError(
            "An active connection with this name or the default flag already exists",
            details={"name": name},
        ) from exc
    append_platform_event(
        session,
        actor=_actor(actor, workspace_id=workspace_id),
        action="model_provider_connection.created",
        target_type="model_provider_connection",
        target_id=str(connection.id),
        summary="Model provider connection created",
        details={"name": name, "default_model": default_model, "is_default": is_default},
    )
    return connection


def list_connections(
    session: Session, *, actor: CurrentUser, workspace_id: UUID, page: PageParams
) -> tuple[list[ModelProviderConnection], int]:
    access.require_workspace_member(session, workspace_id=workspace_id, user_id=actor.user_id)
    statement = (
        select(ModelProviderConnection)
        .where(ModelProviderConnection.workspace_id == workspace_id)
        .order_by(ModelProviderConnection.created_at.desc(), ModelProviderConnection.id)
    )
    return page_scalars(session, statement, page)


def get_connection(
    session: Session, *, actor: CurrentUser, workspace_id: UUID, connection_id: UUID
) -> ModelProviderConnection:
    access.require_workspace_member(session, workspace_id=workspace_id, user_id=actor.user_id)
    return _connection(session, workspace_id=workspace_id, connection_id=connection_id)


def update_connection(
    session: Session,
    *,
    actor: CurrentUser,
    workspace_id: UUID,
    connection_id: UUID,
    name: str | None,
    base_url: str | None,
    default_model: str | None,
    models: list[str] | None,
    is_default: bool | None,
) -> ModelProviderConnection:
    """Change one connection's catalog, endpoint or default selection."""
    access.require_workspace_member(
        session, workspace_id=workspace_id, user_id=actor.user_id, permission="manage", lock=True
    )
    connection = _connection(session, workspace_id=workspace_id, connection_id=connection_id)
    if connection.status != CONNECTION_STATUS_ACTIVE:
        raise ConflictError(
            "A disabled connection cannot be changed", details={"status": connection.status}
        )
    if name is not None:
        connection.name = name
    if base_url is not None:
        connection.base_url = _validated_base_url(base_url)
    next_models = _validated_models(models) if models is not None else list(connection.models)
    next_default = default_model if default_model is not None else connection.default_model
    if next_default not in next_models:
        raise InvalidArgumentError(
            "The default model must be one of the declared models",
            details={"default_model": next_default},
        )
    connection.models = next_models
    connection.default_model = next_default
    if is_default is not None and is_default != connection.is_default:
        if is_default:
            _unset_other_defaults(session, workspace_id=workspace_id)
        connection.is_default = is_default
    append_platform_event(
        session,
        actor=_actor(actor, workspace_id=workspace_id),
        action="model_provider_connection.updated",
        target_type="model_provider_connection",
        target_id=str(connection.id),
        summary="Model provider connection updated",
        details={"default_model": connection.default_model, "is_default": connection.is_default},
    )
    return connection


def set_default_connection(
    session: Session, *, actor: CurrentUser, workspace_id: UUID, connection_id: UUID
) -> ModelProviderConnection:
    """Make one connection the workspace default; the previous one steps down."""
    access.require_workspace_member(
        session, workspace_id=workspace_id, user_id=actor.user_id, permission="manage", lock=True
    )
    connection = _connection(session, workspace_id=workspace_id, connection_id=connection_id)
    if connection.status != CONNECTION_STATUS_ACTIVE:
        raise ConflictError(
            "A disabled connection cannot become the default",
            details={"status": connection.status},
        )
    if not connection.is_default:
        _unset_other_defaults(session, workspace_id=workspace_id)
        connection.is_default = True
    append_platform_event(
        session,
        actor=_actor(actor, workspace_id=workspace_id),
        action="model_provider_connection.default_set",
        target_type="model_provider_connection",
        target_id=str(connection.id),
        summary="Model provider default connection set",
        details={},
    )
    return connection


def disable_connection(
    session: Session, *, actor: CurrentUser, workspace_id: UUID, connection_id: UUID
) -> ModelProviderConnection:
    """Disable one connection; a disabled default steps down with it."""
    access.require_workspace_member(
        session, workspace_id=workspace_id, user_id=actor.user_id, permission="manage", lock=True
    )
    connection = _connection(session, workspace_id=workspace_id, connection_id=connection_id)
    if connection.status == CONNECTION_STATUS_DISABLED:
        return connection
    connection.status = CONNECTION_STATUS_DISABLED
    connection.is_default = False
    append_platform_event(
        session,
        actor=_actor(actor, workspace_id=workspace_id),
        action="model_provider_connection.disabled",
        target_type="model_provider_connection",
        target_id=str(connection.id),
        summary="Model provider connection disabled",
        details={},
    )
    return connection


def resolve_default_model(
    session: Session, *, actor: CurrentUser, workspace_id: UUID
) -> ModelProviderConnection | None:
    """The workspace's default connection, if one is set; no secret is read."""
    access.require_workspace_member(session, workspace_id=workspace_id, user_id=actor.user_id)
    return session.scalar(
        select(ModelProviderConnection).where(
            ModelProviderConnection.workspace_id == workspace_id,
            ModelProviderConnection.is_default.is_(True),
            ModelProviderConnection.status == CONNECTION_STATUS_ACTIVE,
        )
    )


def start_probe(
    session: Session, *, actor: CurrentUser, workspace_id: UUID, connection_id: UUID
) -> ModelProviderConnection:
    """Mark one connection as probing and stage its durable execution."""
    access.require_workspace_member(
        session, workspace_id=workspace_id, user_id=actor.user_id, permission="manage", lock=True
    )
    connection = _connection(session, workspace_id=workspace_id, connection_id=connection_id)
    if connection.status != CONNECTION_STATUS_ACTIVE:
        raise ConflictError(
            "A disabled connection cannot be probed", details={"status": connection.status}
        )
    connection.last_probe_status = PROBE_RUNNING
    connection.last_probe_detail = None
    stage_execution(
        session,
        ModelProviderProbeExecution(
            execution_id=connection.id,
            workflow_id=f"model-provider-probe:{connection.id}",
            connection_id=connection.id,
        ),
    )
    append_platform_event(
        session,
        actor=_actor(actor, workspace_id=workspace_id),
        action="model_provider_connection.probe_started",
        target_type="model_provider_connection",
        target_id=str(connection.id),
        summary="Model provider connection probe started",
        details={},
    )
    return connection


def record_probe_outcome(
    session: Session,
    *,
    connection_id: UUID,
    status: str,
    detail: str | None,
) -> None:
    """Apply one probe outcome; the worker calls this, never a route."""
    connection = session.get(ModelProviderConnection, connection_id)
    if connection is None:
        raise NotFoundError("The model provider connection does not exist")
    if connection.status != CONNECTION_STATUS_ACTIVE:
        return
    connection.last_probe_status = status
    connection.last_probe_detail = detail
    connection.last_probe_at = datetime.now(UTC)


def read_probe(
    session: Session, *, actor: CurrentUser, workspace_id: UUID, connection_id: UUID
) -> ModelProviderConnection:
    """Read one connection's latest probe state."""
    access.require_workspace_member(session, workspace_id=workspace_id, user_id=actor.user_id)
    return _connection(session, workspace_id=workspace_id, connection_id=connection_id)


def _connection(
    session: Session, *, workspace_id: UUID, connection_id: UUID
) -> ModelProviderConnection:
    connection = session.scalar(
        select(ModelProviderConnection).where(
            ModelProviderConnection.id == connection_id,
            ModelProviderConnection.workspace_id == workspace_id,
        )
    )
    if connection is None:
        raise NotFoundError("The model provider connection does not exist")
    return connection


def _require_provider_credential(
    session: Session, *, workspace_id: UUID, credential_id: UUID
) -> None:
    credential = session.scalar(
        select(WorkspaceCredential).where(
            WorkspaceCredential.id == credential_id,
            WorkspaceCredential.workspace_id == workspace_id,
        )
    )
    if credential is None:
        raise NotFoundError("The workspace credential does not exist")
    if (
        credential.kind != CREDENTIAL_KIND_MODEL_PROVIDER
        or credential.status != CREDENTIAL_STATUS_ACTIVE
    ):
        raise InvalidArgumentError(
            "The connection requires an active model provider credential",
            details={"credential_id": str(credential_id)},
        )


def _validated_base_url(base_url: str | None) -> str | None:
    """Check one provider endpoint against the HTTPS egress policy.

    The egress policy speaks in ``EgressURLValidationError``, a ``ValueError``
    the HTTP boundary does not map; a rejected endpoint is a request
    argument problem, so it is converted to the domain error here, at the
    service edge every write passes through.
    """
    if base_url is None:
        return None
    try:
        return validate_egress_url(base_url, policy=BASE_URL_POLICY)
    except EgressURLValidationError as exc:
        raise InvalidArgumentError(
            "The provider base URL is not allowed", details={"reason": str(exc)}
        ) from exc


def _validated_models(models: list[str]) -> list[str]:
    validated: list[str] = []
    for model in models:
        if not model.strip():
            raise InvalidArgumentError("A declared model cannot be empty")
        if model in validated:
            raise InvalidArgumentError("A model is declared twice", details={"model": model})
        validated.append(model)
    if len(validated) > MAX_MODELS:
        raise InvalidArgumentError("Too many declared models")
    return validated


def _unset_other_defaults(session: Session, *, workspace_id: UUID) -> None:
    """Step the current default down before another connection takes the flag.

    The demotion is an explicit statement executed now, not an ORM change
    the unit of work would batch with the promotion: two updates of one
    table carry no ordering guarantee, and the single-default index refuses
    two defaults at any instant.
    """
    session.execute(
        update(ModelProviderConnection)
        .where(
            ModelProviderConnection.workspace_id == workspace_id,
            ModelProviderConnection.is_default.is_(True),
        )
        .values(is_default=False)
    )


def _actor(actor: CurrentUser, *, workspace_id: UUID) -> AuditActor:
    return AuditActor(actor_type="user", actor_id=str(actor.user_id), workspace_id=workspace_id)


__all__ = [
    "BASE_URL_POLICY",
    "DEFAULT_OPENAI_BASE_URL",
    "MODEL_PROVIDER_PROBE_WORKFLOW",
    "PROBE_FAILED",
    "ModelProviderProbeExecution",
    "create_connection",
    "disable_connection",
    "get_connection",
    "list_connections",
    "read_probe",
    "record_probe_outcome",
    "resolve_default_model",
    "set_default_connection",
    "start_probe",
    "update_connection",
]
