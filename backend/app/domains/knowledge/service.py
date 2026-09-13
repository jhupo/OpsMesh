from __future__ import annotations

import hashlib
import ipaddress
import json
from collections.abc import Mapping
from dataclasses import replace
from typing import cast
from urllib.parse import parse_qsl, urlsplit, urlunsplit
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.db.errors import commit_or_raise_conflict, flush_or_raise_conflict
from backend.app.core.db.pagination import page_scalars_by_offset
from backend.app.core.security.redaction import is_sensitive_payload_key
from backend.app.domains.capabilities.resources.schema import reject_embedded_secrets
from backend.app.domains.knowledge.contracts import (
    KnowledgeSourceCreate,
    KnowledgeSourceStatus,
    KnowledgeSourceType,
    KnowledgeSourceUpdate,
)
from backend.app.domains.knowledge.models import KnowledgeSource, KnowledgeSourceRevision
from backend.app.domains.workspace.storage.models import WorkspaceFile
from backend.app.observability.audit_service import AuditService


class KnowledgeSourceVersionConflictError(ValueError):
    """The caller attempted to update an outdated source version."""


class KnowledgeSourceService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_sources(
        self,
        workspace_id: UUID,
        *,
        limit: int,
        offset: int,
        include_archived: bool = False,
    ) -> tuple[list[KnowledgeSource], int]:
        statement = select(KnowledgeSource).where(KnowledgeSource.workspace_id == workspace_id)
        if not include_archived:
            statement = statement.where(KnowledgeSource.status != "archived")
        return page_scalars_by_offset(
            self._session,
            statement.order_by(KnowledgeSource.created_at.desc(), KnowledgeSource.id.desc()),
            limit=limit,
            offset=offset,
        )

    def get_source(
        self,
        workspace_id: UUID,
        source_id: UUID,
        *,
        include_archived: bool = False,
    ) -> KnowledgeSource | None:
        statement = select(KnowledgeSource).where(
            KnowledgeSource.workspace_id == workspace_id,
            KnowledgeSource.id == source_id,
        )
        if not include_archived:
            statement = statement.where(KnowledgeSource.status != "archived")
        return self._session.scalar(statement)

    def create(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        command: KnowledgeSourceCreate,
    ) -> KnowledgeSource:
        normalized = self._normalize_source(workspace_id, command)
        source = KnowledgeSource(
            workspace_id=workspace_id,
            created_by_user_id=actor_user_id,
            name=_normalize_name(normalized.name),
            description=_normalize_description(normalized.description),
            source_type=normalized.source_type,
            uri=normalized.uri,
            workspace_file_id=normalized.workspace_file_id,
            source_config=_normalize_config(normalized.config),
            source_fingerprint=_fingerprint(normalized),
            version=1,
            status="active",
        )
        self._session.add(source)
        flush_or_raise_conflict(self._session, "Knowledge source name already exists")
        self._record_revision(source, actor_user_id, reason="created")
        self._audit(source, actor_user_id, "knowledge_source.created")
        commit_or_raise_conflict(self._session, "Knowledge source name already exists")
        self._session.refresh(source)
        return source

    def update(
        self,
        *,
        workspace_id: UUID,
        source_id: UUID,
        actor_user_id: UUID,
        command: KnowledgeSourceUpdate,
    ) -> KnowledgeSource | None:
        source = self._lock_source(workspace_id, source_id)
        if source is None:
            return None
        if source.status == "archived":
            raise ValueError("Archived knowledge source cannot be updated")
        if source.version != command.expected_version:
            raise KnowledgeSourceVersionConflictError("Knowledge source version is stale")

        candidate = KnowledgeSourceCreate(
            name=command.name if command.name is not None else source.name,
            description=command.description
            if command.description is not None
            else source.description,
            source_type=command.source_type
            or cast(KnowledgeSourceType, source.source_type),
            uri=command.uri if command.source_type or command.uri is not None else source.uri,
            workspace_file_id=(
                command.workspace_file_id
                if command.source_type or command.workspace_file_id is not None
                else source.workspace_file_id
            ),
            config=command.config if command.config is not None else dict(source.source_config),
        )
        normalized = self._normalize_source(workspace_id, candidate)
        old_fingerprint = source.source_fingerprint
        source.name = _normalize_name(normalized.name)
        source.description = _normalize_description(normalized.description)
        source.source_type = normalized.source_type
        source.uri = normalized.uri
        source.workspace_file_id = normalized.workspace_file_id
        source.source_config = _normalize_config(normalized.config)
        source.source_fingerprint = _fingerprint(normalized)
        source.version += 1
        if command.status is not None:
            source.status = command.status
        if source.source_fingerprint != old_fingerprint:
            source.last_ingested_at = None
            source.last_error_code = None
        self._record_revision(source, actor_user_id, reason="updated")
        self._audit(
            source,
            actor_user_id,
            "knowledge_source.updated",
            metadata={
                "version": source.version,
                "source_changed": source.source_fingerprint != old_fingerprint,
            },
        )
        commit_or_raise_conflict(self._session, "Knowledge source name already exists")
        self._session.refresh(source)
        return source

    def set_status(
        self,
        *,
        workspace_id: UUID,
        source_id: UUID,
        actor_user_id: UUID,
        expected_version: int,
        status: KnowledgeSourceStatus,
    ) -> KnowledgeSource | None:
        source = self._lock_source(workspace_id, source_id)
        if source is None:
            return None
        if source.status == "archived" and status != "archived":
            raise ValueError("Archived knowledge source cannot be resumed")
        if source.version != expected_version:
            raise KnowledgeSourceVersionConflictError("Knowledge source version is stale")
        if status == "archived" and source.status == "archived":
            return source
        source.status = status
        source.version += 1
        self._record_revision(source, actor_user_id, reason=f"status:{status}")
        self._audit(
            source,
            actor_user_id,
            f"knowledge_source.{status}",
            metadata={"version": source.version},
        )
        self._session.commit()
        self._session.refresh(source)
        return source

    def _lock_source(self, workspace_id: UUID, source_id: UUID) -> KnowledgeSource | None:
        return self._session.scalar(
            select(KnowledgeSource)
            .where(
                KnowledgeSource.workspace_id == workspace_id,
                KnowledgeSource.id == source_id,
            )
            .with_for_update()
        )

    def list_revisions(
        self,
        *,
        workspace_id: UUID,
        source_id: UUID,
        limit: int,
        offset: int,
    ) -> tuple[list[KnowledgeSourceRevision], int]:
        statement = select(KnowledgeSourceRevision).where(
            KnowledgeSourceRevision.workspace_id == workspace_id,
            KnowledgeSourceRevision.source_id == source_id,
        )
        return page_scalars_by_offset(
            self._session,
            statement.order_by(
                KnowledgeSourceRevision.version.desc(),
                KnowledgeSourceRevision.id.desc(),
            ),
            limit=limit,
            offset=offset,
        )

    def get_revision(
        self,
        *,
        workspace_id: UUID,
        source_id: UUID,
        version: int,
    ) -> KnowledgeSourceRevision | None:
        return self._session.scalar(
            select(KnowledgeSourceRevision).where(
                KnowledgeSourceRevision.workspace_id == workspace_id,
                KnowledgeSourceRevision.source_id == source_id,
                KnowledgeSourceRevision.version == version,
            )
        )

    def _record_revision(
        self,
        source: KnowledgeSource,
        actor_user_id: UUID,
        *,
        reason: str,
    ) -> None:
        self._session.add(
            KnowledgeSourceRevision(
                workspace_id=source.workspace_id,
                source_id=source.id,
                changed_by_user_id=actor_user_id,
                version=source.version,
                name=source.name,
                description=source.description,
                source_type=source.source_type,
                uri=source.uri,
                workspace_file_id=source.workspace_file_id,
                source_config=dict(source.source_config),
                source_fingerprint=source.source_fingerprint,
                status=source.status,
                change_reason=reason,
            )
        )

    def _normalize_source(
        self,
        workspace_id: UUID,
        command: KnowledgeSourceCreate,
    ) -> KnowledgeSourceCreate:
        source_type = command.source_type
        if source_type not in {"url", "workspace_file"}:
            raise ValueError("Unsupported knowledge source type")
        uri = _normalize_uri(command.uri) if source_type == "url" else None
        workspace_file_id = command.workspace_file_id if source_type == "workspace_file" else None
        if source_type == "url" and uri is None:
            raise ValueError("URL knowledge source requires uri")
        if source_type == "workspace_file" and workspace_file_id is None:
            raise ValueError("Workspace-file knowledge source requires workspace_file_id")
        if source_type == "url" and command.workspace_file_id is not None:
            raise ValueError("URL knowledge source cannot include workspace_file_id")
        if source_type == "workspace_file" and command.uri is not None:
            raise ValueError("Workspace-file knowledge source cannot include uri")
        if workspace_file_id is not None:
            file = self._session.scalar(
                select(WorkspaceFile).where(
                    WorkspaceFile.workspace_id == workspace_id,
                    WorkspaceFile.id == workspace_file_id,
                    WorkspaceFile.status == "active",
                )
            )
            if file is None:
                raise ValueError("Workspace file not found")
        return replace(
            command,
            name=_normalize_name(command.name),
            description=_normalize_description(command.description),
            uri=uri,
            workspace_file_id=workspace_file_id,
            config=_normalize_config(command.config),
        )

    def _audit(
        self,
        source: KnowledgeSource,
        actor_user_id: UUID,
        action: str,
        *,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        details: dict[str, object] = {
            "source_type": source.source_type,
            "version": source.version,
            "status": source.status,
        }
        if metadata:
            details.update(metadata)
        AuditService(self._session).record_user_action(
            workspace_id=source.workspace_id,
            user_id=actor_user_id,
            action=action,
            target_type="knowledge_source",
            target_id=source.id,
            metadata=details,
        )


def _normalize_name(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > 160:
        raise ValueError("Knowledge source name must contain 1-160 characters")
    return normalized


def _normalize_description(value: str) -> str:
    normalized = value.strip()
    if len(normalized) > 2_000:
        raise ValueError("Knowledge source description is too long")
    return normalized


def _normalize_uri(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    parsed = urlsplit(normalized)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Knowledge source uri must be an https URL")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Knowledge source uri cannot contain credentials or fragments")
    hostname = parsed.hostname.lower().rstrip(".")
    if hostname in {"localhost", "host.docker.internal", "metadata.google.internal"}:
        raise ValueError("Knowledge source uri cannot target an internal host")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and (address.is_private or address.is_loopback or address.is_link_local):
        raise ValueError("Knowledge source uri cannot target a private network address")
    for key, _ in parse_qsl(parsed.query, keep_blank_values=True):
        if is_sensitive_payload_key(key):
            raise ValueError("Knowledge source uri query cannot contain secret parameters")
    return urlunsplit(("https", parsed.netloc, parsed.path or "/", parsed.query, ""))


def _normalize_config(config: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(config, Mapping):
        raise ValueError("Knowledge source config must be an object")
    if len(config) > 32:
        raise ValueError("Knowledge source config has too many fields")
    reject_embedded_secrets(dict(config), path="config")
    try:
        encoded = json.dumps(dict(config), sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Knowledge source config must contain JSON values") from exc
    if len(encoded.encode("utf-8")) > 16_384:
        raise ValueError("Knowledge source config is too large")
    return dict(config)


def _fingerprint(command: KnowledgeSourceCreate) -> str:
    payload = {
        "source_type": command.source_type,
        "uri": command.uri,
        "workspace_file_id": str(command.workspace_file_id)
        if command.workspace_file_id is not None
        else None,
        "config": _normalize_config(command.config),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
