"""Author submissions and administrator decisions for public capability releases."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status
from sqlalchemy.orm import Session

from opsmesh.capabilities import lifecycle
from opsmesh.capabilities.models import CapabilityDefinition, CapabilityVersion
from opsmesh.capabilities.publication import KIND, PublicationReview, get_review
from opsmesh.capabilities.schemas import (
    PublicationReviewDecision,
    PublicationReviewSubmission,
    PublicationReviewView,
    version_view,
)
from opsmesh.identity.access import PlatformRole, require_platform_admin
from opsmesh.identity.auth.dependencies import CurrentUserDep, DbSession
from opsmesh.shared.approvals import approvals_for
from opsmesh.shared.audit import AuditActor
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.errors import NotFoundError
from opsmesh.shared.pagination import MAX_PAGE_LIMIT

router = APIRouter(prefix="/capabilities", tags=["capabilities-publication-reviews"])


def _review_view(session: Session, review: PublicationReview) -> PublicationReviewView:
    version = session.get(CapabilityVersion, review.version_id)
    definition = session.get(CapabilityDefinition, version.definition_id) if version else None
    if version is None or definition is None:
        raise NotFoundError("The publication review target does not exist")
    package_filename = lifecycle.version_package_filename(session, version_id=version.id)
    return PublicationReviewView(
        review_id=review.id,
        definition_id=definition.id,
        version_id=version.id,
        version_number=version.version,
        capability_type=definition.type,
        key=definition.key,
        name=definition.name,
        status=review.status,
        requested_by_user_id=review.requested_by_user_id,
        submission_note=review.submission_note,
        reviewed_by_user_id=review.reviewed_by_user_id,
        decision_note=review.decision_note,
        created_at=review.created_at,
        reviewed_at=review.reviewed_at,
        version=version_view(version, package_filename=package_filename),
    )


@router.post(
    "/definitions/{definition_id}/versions/{version_number}/publication-review",
    response_model=PublicationReviewView,
    status_code=status.HTTP_201_CREATED,
    operation_id="capabilities.publication_reviews.submit",
)
def submit_publication_review(
    definition_id: UUID,
    version_number: int,
    payload: PublicationReviewSubmission,
    session: DbSession,
    user: CurrentUserDep,
) -> PublicationReviewView:
    with session_transaction(session):
        review = lifecycle.submit_publication_review(
            session,
            definition_id=definition_id,
            version_number=version_number,
            actor=AuditActor.from_user(user),
            submission_note=payload.submission_note,
        )
    return _review_view(session, review)


@router.get(
    "/publication-reviews/mine",
    response_model=list[PublicationReviewView],
    operation_id="capabilities.publication_reviews.mine",
)
def list_my_publication_reviews(
    session: DbSession,
    user: CurrentUserDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[PublicationReviewView]:
    records, _ = approvals_for(session).search(
        session, kind=KIND, requester_user_id=user.user_id, limit=limit, offset=offset
    )
    reviews = [PublicationReview(record) for record in records]
    return [_review_view(session, review) for review in reviews]


@router.get(
    "/publication-reviews/admin",
    response_model=list[PublicationReviewView],
    operation_id="capabilities.publication_reviews.admin_list",
)
def list_publication_reviews_for_admin(
    session: DbSession,
    user: CurrentUserDep,
    review_status: Annotated[
        str, Query(alias="status", pattern="^(pending|approved|rejected|cancelled)$")
    ] = "pending",
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[PublicationReviewView]:
    require_platform_admin(user)
    records, _ = approvals_for(session).search(
        session, kind=KIND, status=review_status, limit=limit, offset=offset
    )
    reviews = [PublicationReview(record) for record in records]
    return [_review_view(session, review) for review in reviews]


@router.post(
    "/publication-reviews/admin/{review_id}/decision",
    response_model=PublicationReviewView,
    operation_id="capabilities.publication_reviews.decide",
)
def decide_publication_review(
    review_id: UUID,
    payload: PublicationReviewDecision,
    session: DbSession,
    user: CurrentUserDep,
) -> PublicationReviewView:
    require_platform_admin(user)
    with session_transaction(session):
        review = lifecycle.decide_publication_review(
            session,
            review_id=review_id,
            actor=AuditActor.from_user(user),
            actor_is_platform_admin=user.has_platform_role(PlatformRole.ADMIN),
            decision=payload.decision,
            decision_note=payload.decision_note,
        )
    return _review_view(session, review)


@router.post(
    "/publication-reviews/{review_id}/withdraw",
    response_model=PublicationReviewView,
    operation_id="capabilities.publication_reviews.withdraw",
)
def withdraw_publication_review(
    review_id: UUID, session: DbSession, user: CurrentUserDep
) -> PublicationReviewView:
    with session_transaction(session):
        review = lifecycle.withdraw_publication_review(
            session, review_id=review_id, actor=AuditActor.from_user(user)
        )
    return _review_view(session, review)


@router.get(
    "/publication-reviews/{review_id}",
    response_model=PublicationReviewView,
    operation_id="capabilities.publication_reviews.read",
)
def read_publication_review(
    review_id: UUID, session: DbSession, user: CurrentUserDep
) -> PublicationReviewView:
    review = get_review(session, review_id)
    if review is None or (
        review.requested_by_user_id != user.user_id
        and not user.has_platform_role(PlatformRole.ADMIN)
    ):
        raise NotFoundError("The publication review does not exist")
    return _review_view(session, review)
