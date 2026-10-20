"""Model management, current access checks and narrowly scoped key resolution.

All mutations use their caller's transaction. Key resolution is injected into
model I/O and never used to build a Workflow input or a public response.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from opsmesh.identity.access import ensure_active_user, require_platform_admin
from opsmesh.identity.context import CurrentUser
from opsmesh.identity.users.models import UserProfile
from opsmesh.platform.model_connections.models import (
    CONNECTION_STATUS_ACTIVE,
    CONNECTION_STATUS_DISABLED,
    MAX_MODELS,
    PROBE_NEEDS_ATTENTION,
    PROBE_RUNNING,
    ModelConnectionGrant,
    ModelProviderConnection,
)
from opsmesh.shared.audit import AuditActor, AuditRecord, record_audit
from opsmesh.shared.db.pagination import page_scalars
from opsmesh.shared.errors import ConflictError, ForbiddenError, InvalidArgumentError, NotFoundError
from opsmesh.shared.pagination import PageParams
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.shared.serialization import canonical_json
from opsmesh.temporal.dispatch.outbox import stage_execution

MODEL_PROVIDER_PROBE_WORKFLOW = "ModelProviderProbeWorkflow"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"


class ModelProviderProbeExecution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_id: UUID
    workflow_id: str
    workflow_name: str = MODEL_PROVIDER_PROBE_WORKFLOW
    connection_id: UUID
    revision: int


@dataclass(frozen=True)
class ModelSelection:
    """Safe immutable selection for execution; it carries no key or ciphertext."""

    connection_id: UUID
    revision: int
    provider: str
    base_url: str
    model: str


def _models(models: list[str], default_model: str | None = None) -> list[str]:
    if (
        not models
        or len(models) > MAX_MODELS
        or len(set(models)) != len(models)
        or any(not model.strip() or model != model.strip() or len(model) > 200 for model in models)
        or (default_model is not None and default_model not in models)
    ):
        raise InvalidArgumentError("Declare a nonempty unique model list containing the default")
    return list(models)


def _base_url(value: str) -> str:
    # This validates configuration syntax, without introducing runtime network policy.
    from urllib.parse import urlsplit

    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError as exc:
        raise InvalidArgumentError("The model upstream port is invalid") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or len(value) > 500
        or any(ord(char) <= 32 for char in value)
    ):
        raise InvalidArgumentError("The model upstream URL must not contain credentials or queries")
    return value.rstrip("/")


def _audit(session: Session, actor: CurrentUser, connection_id: UUID, action: str) -> None:
    record_audit(
        session,
        AuditRecord(
            category="operation",
            action=f"model_connection.{action}",
            actor=AuditActor.from_user(actor),
            source="platform",
            target_type="model_connection",
            target_id=str(connection_id),
            summary=f"Model connection {action}",
            payload_json=canonical_json({}),
        ),
    )


def _connection(
    session: Session, connection_id: UUID, *, lock: bool = False
) -> ModelProviderConnection:
    query = select(ModelProviderConnection).where(ModelProviderConnection.id == connection_id)
    if lock:
        query = query.with_for_update()
    result = session.scalar(query.execution_options(populate_existing=True))
    if result is None:
        raise NotFoundError("The model connection does not exist")
    return result


def create_connection(
    session: Session,
    *,
    actor: CurrentUser,
    cipher: SecretCipher,
    name: str,
    api_key: str,
    base_url: str,
    default_model: str,
    models: list[str],
) -> ModelProviderConnection:
    require_platform_admin(actor)
    if not name.strip() or len(name) > 120 or not api_key.strip():
        raise InvalidArgumentError("A model connection requires a name and API key")
    if session.scalar(
        select(ModelProviderConnection.id).where(ModelProviderConnection.name == name)
    ):
        raise ConflictError("A model connection with this name already exists")
    encrypted = cipher.encrypt_payload({"api_key": api_key})
    connection = ModelProviderConnection(
        name=name,
        base_url=_base_url(base_url),
        default_model=default_model,
        models=_models(models, default_model),
        encrypted_key=encrypted.ciphertext,
        key_id=encrypted.key_id,
        key_fingerprint=encrypted.fingerprint,
        created_by_user_id=actor.user_id,
    )
    session.add(connection)
    session.flush()
    _audit(session, actor, connection.id, "created")
    return connection


def get_connection(
    session: Session, *, actor: CurrentUser, connection_id: UUID
) -> ModelProviderConnection:
    require_platform_admin(actor)
    return _connection(session, connection_id)


def list_connections(
    session: Session,
    *,
    actor: CurrentUser,
    page: PageParams,
) -> tuple[list[ModelProviderConnection], int]:
    require_platform_admin(actor)
    return page_scalars(
        session, select(ModelProviderConnection).order_by(ModelProviderConnection.id), page
    )


def update_connection(
    session: Session,
    *,
    actor: CurrentUser,
    cipher: SecretCipher,
    connection_id: UUID,
    expected_revision: int,
    name: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    default_model: str | None = None,
    models: list[str] | None = None,
) -> ModelProviderConnection:
    require_platform_admin(actor)
    connection = _connection(session, connection_id, lock=True)
    if connection.revision != expected_revision or connection.status != CONNECTION_STATUS_ACTIVE:
        raise ConflictError("The connection revision changed or the connection is disabled")
    if name is not None:
        if not name.strip() or len(name) > 120:
            raise InvalidArgumentError("A model connection requires a valid name")
        duplicate = session.scalar(
            select(ModelProviderConnection.id).where(
                ModelProviderConnection.name == name, ModelProviderConnection.id != connection.id
            )
        )
        if duplicate:
            raise ConflictError("A model connection with this name already exists")
        connection.name = name
    if api_key is not None:
        if not api_key.strip():
            raise InvalidArgumentError("The API key cannot be empty")
        encrypted = cipher.encrypt_payload({"api_key": api_key})
        connection.encrypted_key = encrypted.ciphertext
        connection.key_id = encrypted.key_id
        connection.key_fingerprint = encrypted.fingerprint
    if base_url is not None:
        connection.base_url = _base_url(base_url)
    next_default = default_model if default_model is not None else connection.default_model
    connection.models = _models(models if models is not None else connection.models, next_default)
    connection.default_model = next_default
    connection.revision += 1
    connection.last_probe_status = "never"
    connection.probe_id = None
    session.flush()
    _audit(session, actor, connection.id, "updated")
    return connection


def disable_connection(
    session: Session, *, actor: CurrentUser, connection_id: UUID
) -> ModelProviderConnection:
    require_platform_admin(actor)
    connection = _connection(session, connection_id, lock=True)
    if connection.status != CONNECTION_STATUS_DISABLED:
        connection.status = CONNECTION_STATUS_DISABLED
        connection.revision += 1
        connection.probe_id = None
        session.flush()
        _audit(session, actor, connection.id, "disabled")
    return connection


def grant_connection(
    session: Session,
    *,
    actor: CurrentUser,
    connection_id: UUID,
    user_id: UUID,
    models: list[str],
    is_default: bool = False,
) -> ModelConnectionGrant:
    require_platform_admin(actor)
    # Lock the user first: defaults across different upstreams serialize on this row.
    user = session.scalar(select(UserProfile).where(UserProfile.id == user_id).with_for_update())
    if user is None:
        raise NotFoundError("The user does not exist")
    connection = _connection(session, connection_id, lock=True)
    granted = _models(models)
    if connection.status != CONNECTION_STATUS_ACTIVE or not set(granted) <= set(connection.models):
        raise InvalidArgumentError("The grant must refer to active declared models")
    if is_default:
        session.execute(
            update(ModelConnectionGrant)
            .where(ModelConnectionGrant.user_id == user_id)
            .values(is_default=False)
        )
    grant = session.get(ModelConnectionGrant, (connection_id, user_id))
    if grant is None:
        grant = ModelConnectionGrant(connection_id=connection_id, user_id=user_id)
        session.add(grant)
    grant.models = granted
    grant.is_default = is_default
    grant.granted_by_user_id = actor.user_id
    session.flush()
    _audit(session, actor, connection.id, "access_granted")
    return grant


def revoke_connection(
    session: Session, *, actor: CurrentUser, connection_id: UUID, user_id: UUID
) -> None:
    require_platform_admin(actor)
    user = session.scalar(select(UserProfile).where(UserProfile.id == user_id).with_for_update())
    if user is None:
        raise NotFoundError("The user does not exist")
    _connection(session, connection_id, lock=True)
    grant = session.get(ModelConnectionGrant, (connection_id, user_id))
    if grant is not None:
        session.delete(grant)
        _audit(session, actor, connection_id, "access_revoked")


def resolve_connection(
    session: Session,
    *,
    actor: CurrentUser,
    connection_id: UUID,
    model: str | None = None,
) -> ModelSelection:
    ensure_active_user(actor)
    user = session.get(UserProfile, actor.user_id, populate_existing=True)
    if user is None or user.status != "active":
        raise ForbiddenError("The model caller is inactive")
    connection = _connection(session, connection_id)
    grant = session.get(
        ModelConnectionGrant, (connection_id, actor.user_id), populate_existing=True
    )
    if connection.status != CONNECTION_STATUS_ACTIVE or grant is None:
        raise ForbiddenError("The model connection is not granted to this user")
    selected = model or connection.default_model
    if selected not in connection.models or selected not in grant.models:
        raise ForbiddenError("The model is not granted to this user")
    return ModelSelection(
        connection.id, connection.revision, connection.provider, connection.base_url, selected
    )


def resolve_model_secret(
    session: Session,
    *,
    cipher: SecretCipher,
    actor: CurrentUser,
    connection_id: UUID,
    model: str | None = None,
) -> dict[str, object]:
    resolve_connection(session, actor=actor, connection_id=connection_id, model=model)
    connection = _connection(session, connection_id)
    return cipher.decrypt_payload(connection.encrypted_key, key_id=connection.key_id)


def available_connections(
    session: Session, *, actor: CurrentUser
) -> list[tuple[ModelProviderConnection, ModelConnectionGrant]]:
    ensure_active_user(actor)
    return [
        (connection, grant)
        for connection, grant in session.execute(
            select(ModelProviderConnection, ModelConnectionGrant)
            .join(ModelConnectionGrant)
            .where(
                ModelConnectionGrant.user_id == actor.user_id,
                ModelProviderConnection.status == CONNECTION_STATUS_ACTIVE,
            )
            .order_by(ModelProviderConnection.id)
        )
    ]


def start_probe(
    session: Session, *, actor: CurrentUser, connection_id: UUID
) -> ModelProviderConnection:
    require_platform_admin(actor)
    connection = _connection(session, connection_id, lock=True)
    if (
        connection.status != CONNECTION_STATUS_ACTIVE
        or connection.last_probe_status == PROBE_RUNNING
    ):
        raise ConflictError("The connection is disabled or a probe is already running")
    probe_id = uuid4()
    connection.probe_id = probe_id
    connection.last_probe_status = PROBE_RUNNING
    connection.last_probe_detail = None
    stage_execution(
        session,
        ModelProviderProbeExecution(
            execution_id=probe_id,
            workflow_id=f"model-provider-probe:{probe_id}",
            connection_id=connection.id,
            revision=connection.revision,
        ),
    )
    _audit(session, actor, connection.id, "probe_started")
    return connection


def record_probe_outcome(
    session: Session,
    *,
    connection_id: UUID,
    probe_id: UUID,
    revision: int,
    status: str,
    detail: str | None,
) -> None:
    connection = _connection(session, connection_id, lock=True)
    if (
        connection.status == CONNECTION_STATUS_ACTIVE
        and connection.probe_id == probe_id
        and connection.revision == revision
    ):
        if status not in {"ok", "failed", PROBE_NEEDS_ATTENTION}:
            raise InvalidArgumentError("Invalid model probe result")
        connection.last_probe_status = status
        connection.last_probe_detail = detail
        connection.last_probe_at = datetime.now(UTC)


def reencrypt_model_keys(
    session: Session,
    *,
    cipher: SecretCipher,
    cursor: str | None,
    limit: int,
) -> tuple[int, str | None]:
    """Rewrap one ordered page in the caller's transaction, including disabled upstreams."""
    if not 1 <= limit <= 1000:
        raise InvalidArgumentError("The key rotation page limit must be between 1 and 1000")
    query = select(ModelProviderConnection).order_by(ModelProviderConnection.id)
    if cursor is not None:
        try:
            after = UUID(cursor)
        except ValueError as exc:
            raise InvalidArgumentError("Invalid model key rotation cursor") from exc
        query = query.where(ModelProviderConnection.id > after)
    connections = list(session.scalars(query.limit(limit).with_for_update()))
    for connection in connections:
        if connection.key_id == cipher.current_key_id:
            continue
        payload = cipher.decrypt_payload(connection.encrypted_key, key_id=connection.key_id)
        encrypted = cipher.encrypt_payload(payload)
        connection.encrypted_key = encrypted.ciphertext
        connection.key_id = encrypted.key_id
        connection.key_fingerprint = encrypted.fingerprint
    next_cursor = str(connections[-1].id) if len(connections) == limit else None
    return len(connections), next_cursor
