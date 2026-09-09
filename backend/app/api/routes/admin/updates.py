from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from opsmesh_operator.contracts import TAG_PATTERN
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.admin.updates.models import PlatformUpdateEvent, PlatformUpdateJob
from backend.app.admin.updates.service import UpdateService
from backend.app.core.config import Settings, get_settings
from backend.app.db.session import get_db_session
from backend.app.security.service import SecurityAuditService

router = APIRouter(prefix="/system/updates")


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tag: str = Field(pattern=TAG_PATTERN)
    action: Literal["update", "rollback", "backup"] = "update"
    idempotency_key: str = Field(min_length=8, max_length=120, pattern=r"^[A-Za-z0-9_-]+$")


class ApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    action: str
    tag: str
    status: str
    phase: str
    plan: dict[str, object]
    plan_sha256: str | None
    error_code: str | None
    backup_id: str | None
    approved_at: datetime | None
    created_at: datetime
    updated_at: datetime


class EventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    phase: str
    actor: str
    created_at: datetime


@router.post("/plans", response_model=JobResponse, status_code=202)
def create_plan(
    body: PlanRequest,
    request: Request,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> PlatformUpdateJob:
    if not settings.release_update_enabled:
        raise HTTPException(409, "Host updates are disabled")
    try:
        job = UpdateService(session).request(
            tag=body.tag, action=body.action, key=body.idempotency_key
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    _audit(session, request, job, "planned")
    session.commit()
    return job


@router.get("/{job_id}", response_model=JobResponse)
def read_job(job_id: UUID, session: Session = Depends(get_db_session)) -> PlatformUpdateJob:
    try:
        return UpdateService(session).get(job_id)
    except LookupError as exc:
        raise HTTPException(404, "Update job not found") from exc


@router.get("/{job_id}/events", response_model=list[EventResponse])
def read_events(
    job_id: UUID, session: Session = Depends(get_db_session)
) -> list[PlatformUpdateEvent]:
    read_job(job_id, session)
    return list(
        session.scalars(
            select(PlatformUpdateEvent)
            .where(PlatformUpdateEvent.job_id == job_id)
            .order_by(PlatformUpdateEvent.created_at)
        ).all()
    )


@router.post("/{job_id}/apply", response_model=JobResponse, status_code=202)
def apply_job(
    job_id: UUID,
    body: ApplyRequest,
    request: Request,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> PlatformUpdateJob:
    if not settings.release_update_enabled:
        raise HTTPException(409, "Host updates are disabled")
    try:
        job = UpdateService(session).approve(job_id, body.plan_sha256)
    except LookupError as exc:
        raise HTTPException(404, "Update job not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    _audit(session, request, job, "approved")
    session.commit()
    return job


@router.post("/{job_id}/cancel", response_model=JobResponse)
def cancel_job(
    job_id: UUID, request: Request, session: Session = Depends(get_db_session)
) -> PlatformUpdateJob:
    try:
        job = UpdateService(session).cancel(job_id)
    except LookupError as exc:
        raise HTTPException(404, "Update job not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    _audit(session, request, job, "cancelled")
    session.commit()
    return job


def _audit(session: Session, request: Request, job: PlatformUpdateJob, action: str) -> None:
    SecurityAuditService(session).record_request_event(
        request=request,
        action=f"platform.update.{action}",
        outcome="allowed",
        severity="critical",
        reason="Authenticated platform administrator",
        metadata={"job_id": str(job.id), "tag": job.tag, "plan_sha256": job.plan_sha256},
    )
