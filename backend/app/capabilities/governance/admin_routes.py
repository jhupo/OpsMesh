from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from backend.app.capabilities.governance.admin_schemas import (
    AdminCapabilityGovernanceRequest,
    AdminCapabilityGovernanceResponse,
)
from backend.app.capabilities.governance.admin_service import (
    AdminCapabilityGovernanceService,
    AdminCapabilityKind,
)
from backend.app.identity.authorization.admin_dependencies import require_platform_admin
from backend.app.shared.db.session import get_db_session
from backend.app.shared.http.client_ip import security_request_context

router = APIRouter(dependencies=[Depends(require_platform_admin)])


def _set_block(
    kind: AdminCapabilityKind,
    resource_id: UUID,
    payload: AdminCapabilityGovernanceRequest,
    request: Request,
    workspace_id: UUID | None,
    blocked: bool,
    session: Session,
) -> AdminCapabilityGovernanceResponse:
    try:
        state = AdminCapabilityGovernanceService(session).set_block(
            kind,
            resource_id,
            workspace_id=workspace_id,
            blocked=blocked,
            reason=payload.reason,
            request_context=security_request_context(request),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return AdminCapabilityGovernanceResponse.model_validate(state, from_attributes=True)


@router.post(
    "/catalog/{kind}/{resource_id}/block",
    response_model=AdminCapabilityGovernanceResponse,
)
def block_capability_resource(
    kind: AdminCapabilityKind,
    resource_id: UUID,
    payload: AdminCapabilityGovernanceRequest,
    request: Request,
    workspace_id: UUID | None = Query(default=None),
    session: Session = Depends(get_db_session),
) -> AdminCapabilityGovernanceResponse:
    return _set_block(kind, resource_id, payload, request, workspace_id, True, session)


@router.post(
    "/catalog/{kind}/{resource_id}/release",
    response_model=AdminCapabilityGovernanceResponse,
)
def release_capability_resource(
    kind: AdminCapabilityKind,
    resource_id: UUID,
    payload: AdminCapabilityGovernanceRequest,
    request: Request,
    workspace_id: UUID | None = Query(default=None),
    session: Session = Depends(get_db_session),
) -> AdminCapabilityGovernanceResponse:
    return _set_block(kind, resource_id, payload, request, workspace_id, False, session)
