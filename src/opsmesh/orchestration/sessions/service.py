"""Session lifecycle and history operations in the caller's PostgreSQL transaction."""

from __future__ import annotations

from hashlib import sha256
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.orchestration.contracts import ExecutionScope, ExecutionSubject, ScopeAdmission
from opsmesh.orchestration.sessions import history
from opsmesh.orchestration.sessions.access import load_visible_session
from opsmesh.orchestration.sessions.contracts import (
    HistoryMutationResult,
    HistoryOperation,
    SessionRetention,
    SessionRunActivity,
    SessionWriter,
    operation_payload,
)
from opsmesh.orchestration.sessions.history import HistoryGateway
from opsmesh.orchestration.sessions.models import (
    RECEIPT_COMPLETED,
    SESSION_BLOCKED,
    SESSION_CLOSED,
    SESSION_CLOSING,
    SESSION_OPEN,
    SessionHistoryBatch,
    SessionOperationReceipt,
    SessionRecord,
)
from opsmesh.shared.errors import ConflictError
from opsmesh.shared.serialization import payload_digest


def create_session(
    session: Session,
    *,
    subject: ExecutionSubject,
    scope: ExecutionScope,
    admission: ScopeAdmission,
) -> SessionRecord:
    admission.admit(session, subject=subject, scope=scope)
    if scope.configuration_id is not None:
        lock_key = int.from_bytes(
            sha256(f"session:{subject.user_id}:{scope.configuration_id}".encode()).digest()[:8],
            signed=True,
        )
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
        existing = session.scalar(
            select(SessionRecord)
            .where(
                SessionRecord.configuration_id == scope.configuration_id,
                SessionRecord.principal_id == subject.user_id,
                SessionRecord.status != SESSION_CLOSED,
            )
            .with_for_update()
        )
        if existing is not None:
            if existing.status == SESSION_BLOCKED:
                raise ConflictError("The configuration Session is blocked", code="session_blocked")
            if existing.status == SESSION_CLOSING:
                raise ConflictError(
                    "The previous configuration Session is still closing",
                    code="session_closing",
                )
            return existing
    record = SessionRecord(
        scope_id=subject.user_id,
        principal_id=subject.user_id,
        configuration_id=scope.configuration_id,
        owner_ref=f"user:{subject.user_id}",
        status=SESSION_OPEN,
        next_sequence=1,
        history_revision=0,
    )
    session.add(record)
    session.flush()
    return record


def get_session(
    session: Session,
    *,
    subject: ExecutionSubject,
    admission: ScopeAdmission,
    session_id: UUID,
) -> SessionRecord:
    return load_visible_session(
        session, subject=subject, admission=admission, session_id=session_id
    )


def close_session(
    session: Session,
    *,
    subject: ExecutionSubject,
    admission: ScopeAdmission,
    activity: SessionRunActivity,
    session_id: UUID,
) -> SessionRecord:
    record = load_visible_session(
        session, subject=subject, admission=admission, session_id=session_id, lock=True
    )
    if record.status == SESSION_BLOCKED:
        raise ConflictError("The model session is blocked", code="session_blocked")
    if record.status == SESSION_OPEN:
        record.status = SESSION_CLOSING
    if record.status == SESSION_CLOSING and not activity.has_active_runs(
        session, session_id=record.id
    ):
        record.status = SESSION_CLOSED
    session.flush()
    return record


async def delete_session(
    sessions: sessionmaker[Session],
    gateway: HistoryGateway,
    *,
    subject: ExecutionSubject,
    admission: ScopeAdmission,
    session_id: UUID,
    retention: SessionRetention,
) -> None:
    with sessions() as session, session.begin():
        record = load_visible_session(
            session, subject=subject, admission=admission, session_id=session_id, lock=True
        )
        if record.status != SESSION_CLOSED:
            raise ConflictError(
                "Only a closed model session can be deleted", code="session_not_closed"
            )
        if retention.has_retained_runs(session, session_id=record.id):
            raise ConflictError("The model Session is referenced by retained Runs")
        # Native rows and operation receipts cascade in this same transaction.
        session.delete(record)


async def read_history(
    session: Session,
    gateway: HistoryGateway,
    *,
    subject: ExecutionSubject,
    admission: ScopeAdmission,
    session_id: UUID,
    limit: int | None,
    writer: SessionWriter | None = None,
    writer_run_id: UUID | None = None,
) -> list[dict[str, Any]]:
    record = load_visible_session(
        session, subject=subject, admission=admission, session_id=session_id
    )
    if writer_run_id is not None:
        if writer is None:
            raise ConflictError("The native Session writer is not assembled")
        writer.authorize_writer(
            session, session_id=record.id, run_id=writer_run_id, principal_id=subject.user_id
        )
    return history.read_items(session, session_id=record.id, limit=limit, run_id=writer_run_id)


async def operate_history(
    sessions: sessionmaker[Session],
    gateway: HistoryGateway,
    *,
    subject: ExecutionSubject,
    admission: ScopeAdmission,
    activity: SessionRunActivity,
    session_id: UUID,
    operation: HistoryOperation,
    operation_id: str,
    writer: SessionWriter | None = None,
    writer_run_id: UUID | None = None,
) -> HistoryMutationResult:
    """Mutation, revision and replay receipt commit together; no separate SDK transaction."""
    fingerprint = payload_digest(
        {**operation_payload(operation), "run_id": str(writer_run_id) if writer_run_id else None}
    )
    with sessions() as session, session.begin():
        record = load_visible_session(
            session, subject=subject, admission=admission, session_id=session_id, lock=True
        )
        if record.status != SESSION_OPEN and not (
            writer_run_id is not None and record.status == SESSION_CLOSING
        ):
            raise ConflictError("The model session is not open", code="session_not_open")
        batch = None
        if writer_run_id is not None:
            if writer is None:
                raise ConflictError("The native Session writer is not assembled")
            writer.authorize_writer(
                session, session_id=record.id, run_id=writer_run_id, principal_id=subject.user_id
            )
            batch = history.run_batch(session, session_id=record.id, run_id=writer_run_id)
        elif activity.has_active_runs(session, session_id=record.id):
            raise ConflictError("The session has active runs", code="session_has_active_runs")
        receipt = session.get(SessionOperationReceipt, (operation_id, record.id))
        if receipt is not None:
            return _replay(receipt, record=record, fingerprint=fingerprint)
        receipt = SessionOperationReceipt(
            scope_id=record.scope_id,
            operation_id=operation_id,
            session_id=record.id,
            run_id=writer_run_id,
            method=operation.method,
            status=RECEIPT_COMPLETED,
            fingerprint=fingerprint,
        )
        try:
            with session.begin_nested():
                session.add(receipt)
                session.flush()
        except IntegrityError:
            landed = session.get(SessionOperationReceipt, (operation_id, record.id))
            if landed is None:
                raise ConflictError("The history operation already exists") from None
            return _replay(landed, record=record, fingerprint=fingerprint)
        if operation.method == "add_items":
            if batch is None:
                batch = SessionHistoryBatch(session_id=record.id)
                session.add(batch)
                session.flush()
            history.append_items(session, batch=batch, items=list(operation.items))
            if writer_run_id is None:
                history.publish_batch(session, model_session=record, batch=batch)
            result = HistoryMutationResult(appended=len(operation.items))
        elif operation.method == "pop_item":
            item = history.pop_item(session, session_id=record.id, batch=batch)
            if writer_run_id is None:
                record.history_revision += 1
            result = HistoryMutationResult(item=item)
        elif operation.method == "clear_session":
            history.clear_items(session, session_id=record.id, batch=batch)
            record.history_revision += 1
            result = HistoryMutationResult(cleared=True)
        else:
            raise ConflictError("Reads use the Session read operation")
        receipt.result = {
            "appended": result.appended,
            "item": result.item,
            "cleared": result.cleared,
        }
        session.flush()
        return result


def _replay(
    receipt: SessionOperationReceipt, *, record: SessionRecord, fingerprint: str
) -> HistoryMutationResult:
    if receipt.session_id != record.id or receipt.fingerprint != fingerprint:
        raise ConflictError(
            "Idempotency-Key was used with a different request", code="idempotency_key_reused"
        )
    if receipt.status != RECEIPT_COMPLETED or receipt.result is None:
        raise ConflictError(
            "The history operation outcome is unknown", code="history_operation_uncertain"
        )
    result = receipt.result
    appended = result.get("appended")
    item = result.get("item")
    return HistoryMutationResult(
        appended=appended if isinstance(appended, int) else None,
        item=item if isinstance(item, dict) else None,
        cleared=result.get("cleared") is True,
    )
