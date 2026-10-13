from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from opsmesh.governance.audit.models import AuditEvent
from opsmesh.shared.config import Settings, get_settings
from opsmesh.shared.security.redaction import redact_sensitive_payload
from opsmesh.shared.telemetry.request_context import current_evidence_context
from opsmesh.workspaces.management.models import Workspace


@dataclass(frozen=True)
class AuditHashChainVerification:
    checked_events: int
    valid: bool
    broken_event_id: UUID | None = None
    reason: str | None = None


class AuditService:
    """Append tenant audit evidence within the caller's transaction."""

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
        """Append a user action to the workspace hash chain and flush it.

        Serializes audit writers with a workspace row lock, captures the current
        request/trace evidence and redacts metadata before hashing. The borrowed
        Session is flushed, not committed; the caller owns commit and rollback.

        Args:
            workspace_id: Tenant owning the audit chain.
            user_id: User responsible for the action.
            action: Stable product action name.
            target_type: Resource kind; agent_run targets must use UUID IDs.
            target_id: Identifier of the affected resource.
            metadata: Additional evidence, redacted before persistence.

        Returns:
            The appended event with previous and current hashes.

        Raises:
            ValueError: The workspace is absent or an agent_run ID is invalid.
        """
        evidence = current_evidence_context()
        self._lock_workspace(workspace_id)
        created_at = datetime.now(UTC)
        event = AuditEvent(
            id=uuid4(),
            workspace_id=workspace_id,
            actor_type="user",
            actor_id=str(user_id),
            user_id=user_id,
            agent_run_id=_agent_run_id_for_target(target_type, target_id),
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            request_id=evidence.get("request_id"),
            trace_id=evidence.get("trace_id"),
            span_id=evidence.get("span_id"),
            worker_id=evidence.get("worker_id"),
            runtime_id=evidence.get("runtime_id"),
            audit_metadata=redact_sensitive_payload(dict(metadata or {}) | evidence),
            previous_hash=self._latest_hash(workspace_id),
            created_at=created_at,
        )
        event.current_hash = self.calculate_event_hash(event)
        self._session.add(event)
        self._session.flush([event])
        return event

    def record_system_action(
        self,
        *,
        workspace_id: UUID,
        action: str,
        target_type: str,
        target_id: UUID | str,
        metadata: dict[str, object] | None = None,
        actor_id: str = "opsmesh.worker",
    ) -> AuditEvent:
        """Append a system action to the workspace hash chain and flush it.

        Uses the same workspace lock, trace evidence and redaction policy as
        record_user_action. The caller owns commit/rollback of the borrowed
        Session, so evidence is persisted atomically with the business change.

        Args:
            workspace_id: Tenant owning the audit chain.
            action: Stable product action name.
            target_type: Resource kind; agent_run targets must use UUID IDs.
            target_id: Identifier of the affected resource.
            metadata: Additional evidence, redacted before persistence.
            actor_id: Identifier of the system component performing the action.

        Returns:
            The appended event with previous and current hashes.

        Raises:
            ValueError: The workspace is absent or an agent_run ID is invalid.
        """
        evidence = current_evidence_context()
        self._lock_workspace(workspace_id)
        created_at = datetime.now(UTC)
        event = AuditEvent(
            id=uuid4(),
            workspace_id=workspace_id,
            actor_type="system",
            actor_id=actor_id,
            user_id=None,
            agent_run_id=_agent_run_id_for_target(target_type, target_id),
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            request_id=evidence.get("request_id"),
            trace_id=evidence.get("trace_id"),
            span_id=evidence.get("span_id"),
            worker_id=evidence.get("worker_id"),
            runtime_id=evidence.get("runtime_id"),
            audit_metadata=redact_sensitive_payload(dict(metadata or {}) | evidence),
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
        # FK inserts already hold KEY SHARE on this row. NO KEY UPDATE still
        # serializes audit writers without two writers upgrading into a deadlock.
        workspace = self._session.scalar(
            select(Workspace.id).where(Workspace.id == workspace_id).with_for_update(key_share=True)
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


def _agent_run_id_for_target(target_type: str, target_id: UUID | str) -> UUID | None:
    if target_type != "agent_run":
        return None
    try:
        return target_id if isinstance(target_id, UUID) else UUID(str(target_id))
    except ValueError as exc:
        raise ValueError("Agent run audit targets must use a UUID target_id") from exc


def _canonical_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        return value.isoformat()
    return value.astimezone(UTC).replace(tzinfo=None).isoformat()
