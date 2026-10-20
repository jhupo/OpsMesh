"""Administrator policy replacement, inspection and pure evaluation API."""

from typing import Annotated

from fastapi import APIRouter, Path, Query
from sqlalchemy import select

from opsmesh.identity.auth.dependencies import CurrentUserDep, DbSession
from opsmesh.platform.policies import service
from opsmesh.platform.policies.models import PlatformPolicy
from opsmesh.platform.policies.schemas import (
    PolicyDecision,
    PolicyEvaluation,
    PolicyView,
    PolicyWrite,
)
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.errors import ForbiddenError

router = APIRouter(prefix="/platform/policies", tags=["platform"])


@router.put("/{key}", response_model=PolicyView, operation_id="platform.policies.put")
def put_policy(
    key: Annotated[str, Path(min_length=1, max_length=100)],
    payload: PolicyWrite,
    session: DbSession,
    subject: CurrentUserDep,
) -> PolicyView:
    with session_transaction(session):
        record = service.replace_policy(session, key=key, payload=payload, subject=subject)
    return PolicyView.model_validate(record)


@router.get("", response_model=list[PolicyView], operation_id="platform.policies.list")
def list_policies(
    session: DbSession,
    subject: CurrentUserDep,
    after: str | None = None,
    limit: int = Query(default=100, ge=1, le=200),
) -> list[PolicyView]:
    if not subject.has_platform_role("admin"):
        raise ForbiddenError("Platform policy administration is required")
    query = select(PlatformPolicy)
    if after is not None:
        query = query.where(PlatformPolicy.key > after)
    return [
        PolicyView.model_validate(row)
        for row in session.scalars(query.order_by(PlatformPolicy.key).limit(limit))
    ]


@router.post("/evaluate", response_model=PolicyDecision, operation_id="platform.policies.evaluate")
def evaluate(
    payload: PolicyEvaluation, session: DbSession, subject: CurrentUserDep
) -> PolicyDecision:
    if not subject.has_platform_role("admin"):
        raise ForbiddenError("Platform policy administration is required")
    return PolicyDecision(
        decision=service.current_decision(session, action=payload.action, name=payload.name)
    )
