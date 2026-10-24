"""Online configuration releases and write-only credential rotation."""

from typing import Annotated

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import select

from opsmesh.agents.catalog import service
from opsmesh.agents.catalog.models import CatalogCredential, CatalogEntry, CatalogVersion
from opsmesh.agents.manager import AgentDefinition
from opsmesh.agents.providers.config import ProviderConfig
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.errors import ConflictError, InvalidArgumentError, NotFoundError
from opsmesh.shared.http.dependencies import secret_cipher
from opsmesh.shared.security.redaction import require_redaction_rules
from opsmesh.users.access import require_platform_admin
from opsmesh.users.auth.dependencies import CurrentUserDep, DbSession

router = APIRouter(prefix="/agent-catalog", tags=["agent-catalog"])


class DraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    value: AgentDefinition | ProviderConfig


class ReleaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    restore_number: int | None = Field(default=None, ge=1)


class CredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    secret: SecretStr = Field(min_length=1, max_length=16384)


def view(row: CatalogEntry) -> dict[str, object]:
    return {
        "id": row.id,
        "kind": row.kind,
        "revision": row.revision,
        "published_number": row.published_number,
        "updated_at": row.updated_at,
    }


@router.get("/credentials", operation_id="agent_catalog.credentials.list")
def credentials(
    session: DbSession,
    user: CurrentUserDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict[str, object]]:
    require_platform_admin(user)
    return [
        {"id": r.id, "revision": r.revision, "updated_at": r.updated_at}
        for r in session.scalars(
            select(CatalogCredential).order_by(CatalogCredential.id).limit(limit).offset(offset)
        )
    ]


@router.put("/credentials/{identifier}", operation_id="agent_catalog.credentials.rotate")
def rotate_credential(
    identifier: str,
    payload: CredentialRequest,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> dict[str, object]:
    require_platform_admin(user)
    service.validate_reference("secret:" + identifier)
    with session_transaction(session):
        service.lock_catalog(session)
        row = session.get(CatalogCredential, identifier)
        if payload.expected_revision != (row.revision if row else 0):
            raise ConflictError("The credential revision changed")
        value = payload.secret.get_secret_value()
        if not value.strip():
            raise InvalidArgumentError("A credential must not be blank")
        encrypted = secret_cipher(request).encrypt_payload({"secret": value})
        if row is None:
            row = CatalogCredential(id=identifier, revision=1)
            session.add(row)
        else:
            row.revision += 1
        row.encrypted_secret, row.key_id = encrypted.ciphertext, encrypted.key_id
        session.flush()
        service.audit(session, user, "credential.rotate", "credential", identifier)
        return {"id": row.id, "revision": row.revision}


@router.get("/{kind}", operation_id="agent_catalog.list")
def entries(
    kind: service.CatalogKind,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict[str, object]]:
    if kind == "provider":
        require_platform_admin(user)
    rows = session.scalars(
        select(CatalogEntry)
        .where(
            CatalogEntry.kind == kind,
            (CatalogEntry.owner_user_id == user.user_id)
            | (CatalogEntry.owner_user_id.is_(None) if user.has_platform_role("admin") else False),
        )
        .order_by(CatalogEntry.id)
        .limit(limit)
        .offset(offset)
    )
    return [view(row) for row in rows]


@router.get("/published/agents", operation_id="agent_catalog.agents.published")
def agents(
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[AgentDefinition]:
    return [
        v
        for v in service.published(
            session, secret_cipher(request), "agent", actor=user, limit=limit, offset=offset
        )
        if isinstance(v, AgentDefinition)
        and (v.owner_user_id in {None, str(user.user_id)} or v.visibility == "public")
    ]


@router.get("/published/models", operation_id="agent_catalog.models.published")
def models(
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict[str, object]]:
    available: list[dict[str, object]] = [
        {
            "provider_id": provider.id,
            "provider_name": provider.name,
            "model": model.model_dump(mode="json"),
        }
        for provider in service.provider_snapshot(session, secret_cipher(request)).list()
        if provider.enabled
        for model in provider.models
        if model.enabled
    ]
    return available[offset : offset + limit]


@router.put("/{kind}/{identifier:path}/draft", operation_id="agent_catalog.draft.save")
def save(
    kind: service.CatalogKind,
    identifier: str,
    payload: DraftRequest,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> dict[str, object]:
    if payload.value.id != identifier:
        raise InvalidArgumentError("The path and configuration identifiers must match")
    with session_transaction(session):
        row = service.save_draft(
            session,
            actor=user,
            cipher=secret_cipher(request),
            kind=kind,
            value=payload.value,
            expected_revision=payload.expected_revision,
            redaction=require_redaction_rules(request.app.state.redaction_rules),
        )
        return view(row)


@router.get("/{kind}/{identifier:path}/draft", operation_id="agent_catalog.draft.read")
def draft(
    kind: service.CatalogKind,
    identifier: str,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> dict[str, object]:
    row = service.entry(session, user, kind, identifier)
    return {
        **view(row),
        "value": service.decode(
            secret_cipher(request), kind, row.encrypted_draft, row.key_id
        ).model_dump(mode="json", by_alias=True),
    }


@router.get("/{kind}/{identifier:path}/versions", operation_id="agent_catalog.versions.list")
def versions(
    kind: service.CatalogKind,
    identifier: str,
    session: DbSession,
    user: CurrentUserDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before: Annotated[int | None, Query(ge=1)] = None,
) -> list[dict[str, object]]:
    service.entry(session, user, kind, identifier)
    query = select(CatalogVersion).where(
        CatalogVersion.kind == kind, CatalogVersion.entry_id == identifier
    )
    if before is not None:
        query = query.where(CatalogVersion.number < before)
    return [
        {"number": v.number, "digest": v.digest, "created_at": v.created_at}
        for v in session.scalars(query.order_by(CatalogVersion.number.desc()).limit(limit))
    ]


@router.get(
    "/{kind}/{identifier:path}/versions/{number}", operation_id="agent_catalog.versions.read"
)
def version(
    kind: service.CatalogKind,
    identifier: str,
    number: int,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> dict[str, object]:
    service.entry(session, user, kind, identifier)
    row = session.get(CatalogVersion, (kind, identifier, number))
    if row is None:
        raise NotFoundError("The configuration release does not exist")
    return {
        "number": row.number,
        "digest": row.digest,
        "value": service.decode(
            secret_cipher(request), kind, row.encrypted_payload, row.key_id
        ).model_dump(mode="json", by_alias=True),
    }


@router.post("/{kind}/{identifier:path}/publish", operation_id="agent_catalog.publish")
def publish(
    kind: service.CatalogKind,
    identifier: str,
    payload: ReleaseRequest,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> dict[str, object]:
    with session_transaction(session):
        release = service.publish(
            session,
            actor=user,
            cipher=secret_cipher(request),
            kind=kind,
            identifier=identifier,
            expected_revision=payload.expected_revision,
            restore_number=payload.restore_number,
        )
        return {"number": release.number, "digest": release.digest}
