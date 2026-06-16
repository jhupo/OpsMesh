from fastapi import APIRouter, Depends, HTTPException, status

from backend.app.api.routes.self_hosted.dependencies import self_hosted_service
from backend.app.api.routes.self_hosted.responses import enrollment_token_response
from backend.app.api.schemas.self_hosted import (
    EnrollmentTokenCreateRequest,
    EnrollmentTokenCreateResponse,
)
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.self_hosted.service import SelfHostedRuntimeService

router = APIRouter()


@router.post(
    "/workspaces/{workspace_id}/self-hosted/enrollment-tokens",
    response_model=EnrollmentTokenCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_enrollment_token(
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
