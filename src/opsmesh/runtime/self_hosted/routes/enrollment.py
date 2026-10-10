from fastapi import APIRouter, Depends, HTTPException, status

from opsmesh.identity.auth.dependencies import workspace_dependency
from opsmesh.identity.authorization.context import WorkspaceContext
from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.runtime.self_hosted.routes.dependencies import self_hosted_service
from opsmesh.runtime.self_hosted.routes.responses import enrollment_token_response
from opsmesh.runtime.self_hosted.schemas import (
    EnrollmentTokenCreateRequest,
    EnrollmentTokenCreateResponse,
)
from opsmesh.runtime.self_hosted.service import SelfHostedRuntimeService

router = APIRouter()


@router.post(
    "/workspaces/{workspace_id}/self-hosted/enrollment-tokens",
    response_model=EnrollmentTokenCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_enrollment_token(
    request: EnrollmentTokenCreateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    service: SelfHostedRuntimeService = Depends(self_hosted_service),
) -> EnrollmentTokenCreateResponse:
    try:
        created = service.create_enrollment_token(
            context.workspace.id,
            context.user.user_id,
            request,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return enrollment_token_response(created)
