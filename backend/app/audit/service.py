from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Select, delete, func, select, text
from sqlalchemy.orm import Session

from backend.app.audit.models import AuditEvent
from backend.app.core.config import Settings, get_settings
from backend.app.security.redaction import redact_sensitive_payload
from backend.app.workspaces.models import Workspace


@dataclass(frozen=True)
class AuditHashChainVerification:
    checked_events: int
    valid: bool
    broken_event_id: UUID | None = None
    reason: str | None = None


@dataclass(frozen=True)
class AuditRetentionCleanupResult:
    retention_days: int | None
    cutoff: datetime | None
    worm_enabled: bool
    eligible_count: int
    deleted_count: int
    retained_count: int
    reason: str


class AuditService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or get_settings()

    def record_user_action(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        action: str,
        target_type: str,
        target_id: UUID | str,
        metadata: dict[str, object] | None = None,
    ) -> AuditEvent:
        created_at = datetime.now(UTC)
        self._lock_workspace(workspace_id)
        event = AuditEvent(
            id=uuid4(),
            workspace_id=workspace_id,
            actor_type="user",
            actor_id=str(user_id),
            user_id=user_id,
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            audit_metadata=redact_sensitive_payload(metadata or {}),
            previous_hash=self._latest_hash(workspace_id),
            created_at=created_at,
        )
        event.current_hash = self.calculate_event_hash(event)
        self._session.add(event)
        self._session.flush([event])
        return event

    def apply_retention_to_statement(
        self,
        statement: Select[tuple[AuditEvent]],
        *,
        now: datetime | None = None,
    ) -> Select[tuple[AuditEvent]]:
        cutoff = self.retention_cutoff(now=now)
        if cutoff is None:
            return statement
        return statement.where(AuditEvent.created_at >= cutoff)

    def retention_cutoff(self, *, now: datetime | None = None) -> datetime | None:
        retention_days = self._settings.audit_event_retention_days
        if retention_days is None:
            return None
        return (now or datetime.now(UTC)) - timedelta(days=retention_days)

    def cleanup_expired_events(
        self,
        *,
        workspace_id: UUID | None = None,
        now: datetime | None = None,
    ) -> AuditRetentionCleanupResult:
        cutoff = self.retention_cutoff(now=now)
        if cutoff is None:
            return AuditRetentionCleanupResult(
                retention_days=None,
                cutoff=None,
                worm_enabled=self._settings.audit_event_worm_enabled,
                eligible_count=0,
                deleted_count=0,
                retained_count=0,
                reason="retention_not_configured",
            )

        filters = [AuditEvent.created_at < cutoff]
        if workspace_id is not None:
            filters.append(AuditEvent.workspace_id == workspace_id)

        eligible_count = int(
            self._session.scalar(
                select(func.count()).select_from(AuditEvent).where(*filters)
            )
            or 0
        )
        if self._settings.audit_event_worm_enabled:
            return AuditRetentionCleanupResult(
                retention_days=self._settings.audit_event_retention_days,
                cutoff=cutoff,
                worm_enabled=True,
                eligible_count=eligible_count,
                deleted_count=0,
                retained_count=eligible_count,
                reason="worm_retained",
            )

        if self._session.get_bind().dialect.name == "postgresql":
            self._session.execute(text("SET LOCAL opsmesh.audit_retention_delete = 'on'"))
        result = self._session.execute(delete(AuditEvent).where(*filters))
        deleted_count = int(getattr(result, "rowcount", 0) or 0)
        self._session.flush()
        return AuditRetentionCleanupResult(
            retention_days=self._settings.audit_event_retention_days,
            cutoff=cutoff,
            worm_enabled=False,
            eligible_count=eligible_count,
            deleted_count=deleted_count,
            retained_count=max(0, eligible_count - deleted_count),
            reason="deleted_expired_events",
        )

    def verify_workspace_hash_chain(self, workspace_id: UUID) -> AuditHashChainVerification:
        events = self._session.scalars(
            select(AuditEvent)
            .where(AuditEvent.workspace_id == workspace_id)
            .order_by(AuditEvent.created_at.asc(), AuditEvent.id.asc())
        ).all()
        # A retention cleanup may remove a valid prefix. The first retained event still
        # authenticates its deleted predecessor through its own hashed previous_hash.
        previous_hash = events[0].previous_hash if events else None
        for index, event in enumerate(events, start=1):
            if event.current_hash is None:
                return AuditHashChainVerification(
                    checked_events=index,
                    valid=False,
                    broken_event_id=event.id,
                    reason="missing_current_hash",
                )
            if event.previous_hash != previous_hash:
                return AuditHashChainVerification(
                    checked_events=index,
                    valid=False,
                    broken_event_id=event.id,
                    reason="previous_hash_mismatch",
                )
            if event.current_hash != self.calculate_event_hash(event):
                return AuditHashChainVerification(
                    checked_events=index,
                    valid=False,
                    broken_event_id=event.id,
                    reason="current_hash_mismatch",
                )
            previous_hash = event.current_hash
        return AuditHashChainVerification(checked_events=len(events), valid=True)

    def _lock_workspace(self, workspace_id: UUID) -> None:
        workspace = self._session.scalar(
            select(Workspace.id).where(Workspace.id == workspace_id).with_for_update()
        )
        if workspace is None:
            raise ValueError("Workspace not found")

    def _latest_hash(self, workspace_id: UUID) -> str | None:
        with self._session.no_autoflush:
            return self._session.scalar(
                select(AuditEvent.current_hash)
                .where(
                    AuditEvent.workspace_id == workspace_id,
                    AuditEvent.current_hash.is_not(None),
                )
                .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
                .limit(1)
            )

    @staticmethod
    def calculate_event_hash(event: AuditEvent) -> str:
        payload = {
            "version": 1,
            "id": str(event.id),
            "workspace_id": str(event.workspace_id),
            "actor_type": event.actor_type,
            "actor_id": event.actor_id,
            "user_id": str(event.user_id) if event.user_id is not None else None,
            "agent_run_id": str(event.agent_run_id) if event.agent_run_id is not None else None,
            "action": event.action,
            "target_type": event.target_type,
            "target_id": event.target_id,
            "audit_metadata": _canonical_value(event.audit_metadata),
            "created_at": _canonical_datetime(event.created_at),
            "previous_hash": event.previous_hash,
        }
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return f"sha256:{sha256(serialized.encode('utf-8')).hexdigest()}"


def _canonical_value(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return _canonical_datetime(value)
    if isinstance(value, dict):
        return {
            str(key): _canonical_value(raw_value)
            for key, raw_value in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, list | tuple):
        return [_canonical_value(item) for item in value]
    return value


def _canonical_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        return value.isoformat()
    return value.astimezone(UTC).replace(tzinfo=None).isoformat()
