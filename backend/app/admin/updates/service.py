from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from opsmesh_operator.contracts import require_tag
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.admin.updates.models import (
    PlatformInstallation,
    PlatformUpdateEvent,
    PlatformUpdateJob,
)


def maintenance_enabled(session: Session) -> bool:
    installation = session.scalar(
        select(PlatformInstallation)
        .where(PlatformInstallation.id == 1)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if installation is None:
        raise RuntimeError("Platform installation state is missing; run database migrations")
    return installation.maintenance


class UpdateService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, job_id: UUID, *, lock: bool = False) -> PlatformUpdateJob:
        query = select(PlatformUpdateJob).where(PlatformUpdateJob.id == job_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        job = self.session.scalar(query)
        if job is None:
            raise LookupError("Update job not found")
        return job

    def request(self, *, tag: str, action: str, key: str) -> PlatformUpdateJob:
        require_tag(tag)
        if action not in {"update", "rollback", "backup"}:
            raise ValueError("Invalid update action")
        installation = self.session.scalar(
            select(PlatformInstallation)
            .where(PlatformInstallation.id == 1)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if installation is None:
            raise ValueError("Installation has not been initialized")
        existing = self.session.scalar(
            select(PlatformUpdateJob).where(PlatformUpdateJob.idempotency_key == key)
        )
        if existing:
            if existing.tag != tag or existing.action != action:
                raise ValueError("Idempotency key was used for another request")
            return existing
        if installation.maintenance or self.session.scalar(
            select(PlatformUpdateJob.id).where(PlatformUpdateJob.active_slot == 1)
        ):
            raise ValueError("Another update or recovery owns the installation")
        job = PlatformUpdateJob(
            id=uuid4(),
            tag=tag,
            action=action,
            idempotency_key=key,
            active_slot=1,
            status="planning",
            phase="requested",
            plan={},
        )
        self.session.add(job)
        self.session.flush()
        self.event(job, "requested", "platform_admin")
        return job

    def approve(self, job_id: UUID, fingerprint: str) -> PlatformUpdateJob:
        job = self.get(job_id, lock=True)
        if job.plan_sha256 != fingerprint:
            raise ValueError("Approval does not match the exact validated plan")
        if job.status in {"queued", "running", "succeeded"} and job.approved_at:
            return job
        if job.status != "ready":
            raise ValueError("Only a ready plan can be approved")
        job.approved_at = datetime.now(UTC)
        job.status = "queued"
        self.event(job, "approved", "platform_admin")
        return job

    def cancel(self, job_id: UUID) -> PlatformUpdateJob:
        job = self.get(job_id, lock=True)
        if job.status not in {"planning", "ready", "queued"}:
            raise ValueError("Running updates require explicit host recovery, not cancellation")
        job.status, job.active_slot = "cancelled", None
        self.event(job, "cancelled", "platform_admin")
        return job

    def event(
        self,
        job: PlatformUpdateJob,
        phase: str,
        actor: str = "host_updater",
        *,
        event_id: UUID | None = None,
        created_at: datetime | None = None,
    ) -> None:
        job.phase = phase
        self.session.add(
            PlatformUpdateEvent(
                id=event_id or uuid4(),
                job_id=job.id,
                phase=phase,
                actor=actor,
                created_at=created_at or datetime.now(UTC),
            )
        )
