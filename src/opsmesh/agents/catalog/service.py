"""Configuration ownership, release validation and fresh runtime snapshots."""

import asyncio
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import and_, select, text
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.agents.catalog.models import CatalogCredential, CatalogEntry, CatalogVersion
from opsmesh.agents.config.sandbox import AgentConfiguration
from opsmesh.agents.manager import AgentDefinition
from opsmesh.agents.providers.config import ProviderConfig
from opsmesh.agents.providers.registry import ProviderRegistry
from opsmesh.agents.resolvers.agents import resolve_agents
from opsmesh.platform.audit.contracts import AuditActor, AuditRecord, record_audit
from opsmesh.shared.errors import ConflictError, InvalidArgumentError, NotFoundError
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.shared.security.redaction import RedactionRules, redact_sensitive_payload
from opsmesh.shared.serialization import canonical_json, payload_digest
from opsmesh.users.access import CurrentUser, require_platform_admin

type CatalogKind = Literal["agent", "provider"]
type CatalogValue = AgentDefinition | ProviderConfig


def lock_catalog(session: Session) -> None:
    session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('agent-catalog', 0))"))


def audit(session: Session, actor: CurrentUser, action: str, kind: str, identifier: str) -> None:
    record_audit(
        session,
        AuditRecord(
            category="agent_catalog",
            action=action,
            actor=AuditActor.from_user(actor),
            source="platform",
            target_type=kind,
            target_id=identifier,
        ),
    )


def may_edit(row: CatalogEntry, actor: CurrentUser) -> bool:
    return row.owner_user_id == actor.user_id or (
        row.owner_user_id is None and actor.has_platform_role("admin")
    )


def entry(session: Session, actor: CurrentUser, kind: CatalogKind, identifier: str) -> CatalogEntry:
    row = session.get(CatalogEntry, (kind, identifier))
    if row is None or not may_edit(row, actor):
        raise NotFoundError("The editable configuration does not exist")
    return row


def decode(cipher: SecretCipher, kind: str, encrypted: str, key_id: str) -> CatalogValue:
    payload = cipher.decrypt_payload(encrypted, key_id=key_id)
    return (AgentDefinition if kind == "agent" else ProviderConfig).model_validate(payload)


def published(
    session: Session,
    cipher: SecretCipher,
    kind: CatalogKind,
    *,
    actor: CurrentUser | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[CatalogValue]:
    query = (
        select(CatalogVersion)
        .join(
            CatalogEntry,
            and_(
                CatalogEntry.kind == CatalogVersion.kind,
                CatalogEntry.id == CatalogVersion.entry_id,
                CatalogEntry.published_number == CatalogVersion.number,
            ),
        )
        .where(CatalogVersion.kind == kind)
        .order_by(CatalogVersion.entry_id)
    )
    if actor is not None:
        query = query.where(
            (CatalogEntry.owner_user_id == actor.user_id)
            | CatalogEntry.owner_user_id.is_(None)
            | (CatalogEntry.visibility == "public")
        )
    if limit is not None:
        query = query.limit(limit).offset(offset)
    versions = session.scalars(query)
    return [decode(cipher, kind, row.encrypted_payload, row.key_id) for row in versions]


def provider_snapshot(session: Session, cipher: SecretCipher) -> ProviderRegistry:
    registry = ProviderRegistry()
    for value in published(session, cipher, "provider"):
        assert isinstance(value, ProviderConfig)
        registry.register(value)
    return registry


def definition_snapshot(
    session: Session,
    cipher: SecretCipher,
    actor: CurrentUser,
) -> Callable[[str], AgentDefinition]:
    definitions = {
        value.id: value
        for value in published(session, cipher, "agent", actor=actor)
        if isinstance(value, AgentDefinition)
        and (value.owner_user_id in {None, str(actor.user_id)} or value.visibility == "public")
    }

    def load(identifier: str) -> AgentDefinition:
        if identifier not in definitions:
            raise NotFoundError("The published Agent configuration does not exist")
        return definitions[identifier].model_copy(deep=True)

    return load


def save_draft(
    session: Session,
    *,
    actor: CurrentUser,
    cipher: SecretCipher,
    kind: CatalogKind,
    value: CatalogValue,
    expected_revision: int,
    redaction: RedactionRules,
) -> CatalogEntry:
    lock_catalog(session)
    if (kind == "agent") != isinstance(value, AgentDefinition):
        raise InvalidArgumentError("Configuration kind and payload do not match")
    if kind == "provider":
        require_platform_admin(actor)
    row = session.get(CatalogEntry, (kind, value.id))
    if row is not None and not may_edit(row, actor):
        raise NotFoundError("The editable configuration does not exist")
    if expected_revision != (row.revision if row else 0):
        raise ConflictError("The configuration revision changed")
    payload = value.model_dump(mode="json", by_alias=True)
    if len(canonical_json(payload).encode()) > 1_000_000:
        raise InvalidArgumentError("The configuration exceeds its size limit")
    checked = {
        k: v
        for k, v in payload.items()
        if not (kind == "provider" and k in {"base_url", "credential_ref"})
    }
    if redact_sensitive_payload(checked, rules=redaction) != checked:
        raise InvalidArgumentError("Configurations must use credential references")
    owner = row.owner_user_id if row else (actor.user_id if kind == "agent" else None)
    if isinstance(value, AgentDefinition):
        payload["owner_user_id"] = str(owner) if owner else None
        payload["created_at"] = row.created_at.isoformat() if row else datetime.now(UTC).isoformat()
        payload["updated_at"] = datetime.now(UTC).isoformat()
    else:
        validate_reference(value.credential_ref)
    encrypted = cipher.encrypt_payload(payload)
    if row is None:
        row = CatalogEntry(
            kind=kind, id=value.id, owner_user_id=owner, revision=1, published_number=0
        )
        session.add(row)
    else:
        row.revision += 1
    row.encrypted_draft, row.key_id = encrypted.ciphertext, encrypted.key_id
    session.flush()
    audit(session, actor, "draft.save", kind, value.id)
    return row


def publish(
    session: Session,
    *,
    actor: CurrentUser,
    cipher: SecretCipher,
    kind: CatalogKind,
    identifier: str,
    expected_revision: int,
    restore_number: int | None = None,
) -> CatalogVersion:
    lock_catalog(session)
    row = entry(session, actor, kind, identifier)
    if row.revision != expected_revision:
        raise ConflictError("The configuration revision changed")
    value = decode(cipher, kind, row.encrypted_draft, row.key_id)
    if restore_number is not None:
        previous = session.get(CatalogVersion, (kind, identifier, restore_number))
        if previous is None:
            raise NotFoundError("The configuration release does not exist")
        value = decode(cipher, kind, previous.encrypted_payload, previous.key_id)
    providers = provider_snapshot(session, cipher)
    if isinstance(value, ProviderConfig):
        require_platform_admin(actor)
        validate_reference(value.credential_ref)
        if (
            value.enabled
            and value.credential_ref.startswith("secret:")
            and session.get(CatalogCredential, value.credential_ref[7:]) is None
        ):
            raise InvalidArgumentError("The provider credential is not configured")
        providers.register(value, replace=True)
    definitions = [v for v in published(session, cipher, "agent") if isinstance(v, AgentDefinition)]
    if isinstance(value, AgentDefinition):
        definitions = [v for v in definitions if v.id != value.id] + [value]
    by_id = {v.id: v for v in definitions}
    # Validate all currently published graphs: disabling a dependency must not break them.
    for root in definitions:
        if not root.enabled:
            continue

        def load(agent_id: str, root: AgentDefinition = root) -> AgentConfiguration:
            target = by_id.get(agent_id)
            if (
                target is None
                or not target.enabled
                or (
                    target.owner_user_id not in {None, root.owner_user_id}
                    and target.visibility != "public"
                )
                or (
                    root.visibility == "public"
                    and target.owner_user_id is not None
                    and target.visibility != "public"
                )
            ):
                raise InvalidArgumentError("An Agent dependency is unavailable to its callers")
            return target.config

        try:
            graph = resolve_agents(root.id, load)
        except (ValueError, KeyError) as exc:
            raise InvalidArgumentError("The published Agent dependency graph is invalid") from exc
        for config in graph.values():
            if config.model is None:
                raise InvalidArgumentError("Published Agents require an explicit model")
            try:
                providers.select(config.model.provider_id, config.model.model_id)
            except (KeyError, ValueError) as exc:
                raise InvalidArgumentError(
                    "A published Agent references an unavailable model"
                ) from exc
    payload = value.model_dump(mode="json", by_alias=True)
    encrypted = cipher.encrypt_payload(payload)
    row.published_number += 1
    row.visibility = value.visibility if isinstance(value, AgentDefinition) else "private"
    row.revision += 1
    if restore_number is not None:
        row.encrypted_draft, row.key_id = encrypted.ciphertext, encrypted.key_id
    release = CatalogVersion(
        kind=kind,
        entry_id=identifier,
        number=row.published_number,
        encrypted_payload=encrypted.ciphertext,
        key_id=encrypted.key_id,
        digest=payload_digest(payload),
    )
    session.add(release)
    session.flush()
    audit(
        session,
        actor,
        "release.rollback" if restore_number else "release.publish",
        kind,
        identifier,
    )
    return release


def validate_reference(reference: str) -> None:
    if not re.fullmatch(r"(?:env:[A-Z_][A-Z0-9_]*|secret:[A-Za-z0-9_.-]{1,190})", reference):
        raise InvalidArgumentError(
            "Credentials require an env:VARIABLE or secret:identifier reference"
        )


class ManagedCatalog:
    """Database-backed runtime view; each build observes a new published snapshot."""

    def __init__(self, sessions: sessionmaker[Session], cipher: SecretCipher) -> None:
        self.sessions, self.cipher = sessions, cipher

    def providers(self) -> ProviderRegistry:
        with self.sessions() as session:
            return provider_snapshot(session, self.cipher)

    async def credential(self, reference: str) -> SecretStr:
        return await asyncio.to_thread(self._credential, reference)

    def _credential(self, reference: str) -> SecretStr:
        validate_reference(reference)
        value: object
        if reference.startswith("env:"):
            value = os.environ.get(reference[4:])
        else:
            with self.sessions() as session:
                row = session.get(CatalogCredential, reference[7:])
                value = (
                    self.cipher.decrypt_payload(row.encrypted_secret, key_id=row.key_id).get(
                        "secret"
                    )
                    if row
                    else None
                )
        if not isinstance(value, str) or not value.strip():
            raise InvalidArgumentError("The model credential is unavailable")
        return SecretStr(value)

    def seed(self, agents: list[AgentDefinition], providers: list[ProviderConfig]) -> None:
        with self.sessions.begin() as session:
            lock_catalog(session)
            for kind, values in (("provider", providers), ("agent", agents)):
                for value in values:
                    if session.get(CatalogEntry, (kind, value.id)) is not None:
                        continue
                    payload = value.model_dump(mode="json", by_alias=True)
                    encrypted = self.cipher.encrypt_payload(payload)
                    session.add(
                        CatalogEntry(
                            kind=kind,
                            id=value.id,
                            owner_user_id=UUID(value.owner_user_id)
                            if isinstance(value, AgentDefinition) and value.owner_user_id
                            else None,
                            revision=1,
                            published_number=1,
                            visibility=value.visibility
                            if isinstance(value, AgentDefinition)
                            else "private",
                            encrypted_draft=encrypted.ciphertext,
                            key_id=encrypted.key_id,
                        )
                    )
                    session.add(
                        CatalogVersion(
                            kind=kind,
                            entry_id=value.id,
                            number=1,
                            encrypted_payload=encrypted.ciphertext,
                            key_id=encrypted.key_id,
                            digest=payload_digest(payload),
                        )
                    )
