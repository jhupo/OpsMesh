from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from opsmesh.identity.authorization.admin_dependencies import require_platform_admin
from opsmesh.identity.authorization.admin_schemas import (
    AdminResourceAuthorizationResponse,
    AdminResourceGrantResponse,
    AdminResourceGrantUpdateRequest,
    AdminResourceOwnerUpdateRequest,
)
from opsmesh.identity.authorization.admin_service import AdminResourceAuthorizationService
from opsmesh.identity.authorization.models import SecuredResource
from opsmesh.identity.authorization.resources import ResourceAction, ResourceKind
from opsmesh.shared.db.session import get_db_session

router = APIRouter(dependencies=[Depends(require_platform_admin)])


def _admin_resource_authorization_response(
    workspace_id: UUID,
    kind: ResourceKind,
    resource_id: UUID,
    resource: SecuredResource,
    grants: list[tuple[UUID, list[ResourceAction]]],
) -> AdminResourceAuthorizationResponse:
    return AdminResourceAuthorizationResponse(
        workspace_id=workspace_id,
        resource_kind=kind,
        resource_id=resource_id,
        owner_user_id=resource.owner_user_id,
        grants=[
            AdminResourceGrantResponse(user_id=user_id, actions=actions)
            for user_id, actions in grants
        ],
    )


@router.get(
    "/workspaces/{workspace_id}/resources/{kind}/{resource_id}/authorization",
    response_model=AdminResourceAuthorizationResponse,
)
def get_admin_resource_authorization(
    workspace_id: UUID,
    kind: ResourceKind,
    resource_id: UUID,
    session: Session = Depends(get_db_session),
) -> AdminResourceAuthorizationResponse:
    authorization = AdminResourceAuthorizationService(session).get_authorization(
        workspace_id,
        kind,
        resource_id,
    )
    if authorization is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")
    resource, grants = authorization
    return _admin_resource_authorization_response(workspace_id, kind, resource_id, resource, grants)


@router.put(
    "/workspaces/{workspace_id}/resources/{kind}/{resource_id}/owner",
    response_model=AdminResourceAuthorizationResponse,
)
def assign_admin_resource_owner(
    workspace_id: UUID,
    kind: ResourceKind,
    resource_id: UUID,
    request: AdminResourceOwnerUpdateRequest,
    session: Session = Depends(get_db_session),
) -> AdminResourceAuthorizationResponse:
    service = AdminResourceAuthorizationService(session)
    try:
        resource = service.assign_owner(workspace_id, kind, resource_id, request.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if resource is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")
    authorization = service.get_authorization(workspace_id, kind, resource_id)
    assert authorization is not None
    current, grants = authorization
    return _admin_resource_authorization_response(workspace_id, kind, resource_id, current, grants)


@router.put(
    "/workspaces/{workspace_id}/resources/{kind}/{resource_id}/grants",
    response_model=AdminResourceAuthorizationResponse,
)
def replace_admin_resource_grants(
    workspace_id: UUID,
    kind: ResourceKind,
    resource_id: UUID,
    request: AdminResourceGrantUpdateRequest,
    session: Session = Depends(get_db_session),
) -> AdminResourceAuthorizationResponse:
    service = AdminResourceAuthorizationService(session)
    try:
        updated = service.replace_grants(
            workspace_id,
            kind,
            resource_id,
            request.user_id,
            request.actions,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")
    authorization = service.get_authorization(workspace_id, kind, resource_id)
    assert authorization is not None
    resource, grants = authorization
    return _admin_resource_authorization_response(workspace_id, kind, resource_id, resource, grants)
