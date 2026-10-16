"""Database batch verification I/O for the audit chain."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session
from temporalio import activity

from opsmesh.governance.audit.contracts import (
    AuditSegmentResult,
    AuditVerificationCompletion,
    AuditVerificationProgress,
    VerifyAuditSegment,
)
from opsmesh.governance.audit.hashing import verify_segment
from opsmesh.governance.audit.models import AuditEvent
from opsmesh.governance.audit.service import (
    complete_verification,
    record_verification_progress,
)


class AuditActivities:
    """The verification I/O; the sessionmaker is injected by bootstrap."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    @activity.defn
    async def verify_audit_segment(self, command: VerifyAuditSegment) -> AuditSegmentResult:
        """Recompute one bounded segment against its anchor and predecessor."""

        def verify(session: Session) -> AuditSegmentResult:
            events = list(
                session.scalars(
                    select(AuditEvent)
                    .where(
                        AuditEvent.sequence >= command.from_sequence,
                        AuditEvent.sequence <= command.to_sequence,
                    )
                    .order_by(AuditEvent.sequence.asc())
                )
            )
            if command.from_sequence == 1:
                checked, broken = verify_segment(
                    events, from_sequence=command.from_sequence, to_sequence=command.to_sequence
                )
                return AuditSegmentResult(checked_count=checked, first_broken_sequence=broken)
            anchor = session.scalar(
                select(AuditEvent).where(AuditEvent.sequence == command.from_sequence - 1)
            )
            if anchor is None:
                # The predecessor is gone; the segment cannot prove its link.
                return AuditSegmentResult(
                    checked_count=0, first_broken_sequence=command.from_sequence
                )
            checked, broken = verify_segment(
                events,
                from_sequence=command.from_sequence,
                to_sequence=command.to_sequence,
                previous_hash=anchor.event_hash,
                previous_sequence=anchor.sequence,
            )
            return AuditSegmentResult(checked_count=checked, first_broken_sequence=broken)

        async with self._sessions.begin() as session:
            return await session.run_sync(verify)

    @activity.defn
    async def record_audit_verification_progress(
        self, command: AuditVerificationProgress
    ) -> None:
        def record(session: Session) -> None:
            record_verification_progress(
                session,
                verification_id=command.verification_id,
                checked_count=command.checked_count,
            )

        async with self._sessions.begin() as session:
            await session.run_sync(record)

    @activity.defn
    async def complete_audit_verification(self, command: AuditVerificationCompletion) -> None:
        def complete(session: Session) -> None:
            complete_verification(
                session,
                verification_id=command.verification_id,
                status=command.status,
                first_broken_sequence=command.first_broken_sequence,
                failure_reason=command.failure_reason,
            )

        async with self._sessions.begin() as session:
            await session.run_sync(complete)
