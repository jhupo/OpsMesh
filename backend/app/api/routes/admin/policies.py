from fastapi import APIRouter, Depends, HTTPException, Query

from backend.app.admin.policy_control import AdminPolicyService
from backend.app.api.pagination import PageResponse, pagination_params
from backend.app.api.routes.admin.dependencies import admin_policy_service
from backend.app.api.routes.admin.responses import page_response
from backend.app.api.schemas.admin import (
    AdminPlatformPolicyEventResponse,
    AdminPlatformPolicyResponse,
    AdminRiskyExecutionPolicyUpdateRequest,
    AdminWorkerControlPolicyUpdateRequest,
)
from backend.app.core.pagination import PageParams

router = APIRouter()


@router.get("/platform-policies", response_model=PageResponse[AdminPlatformPolicyResponse])
async def list_admin_platform_policies(
    page: PageParams = Depends(pagination_params),
    status: str | None = Query(default=None),
    service: AdminPolicyService = Depends(admin_policy_service),
) -> PageResponse[AdminPlatformPolicyResponse]:
    items, total = service.list_platform_policies(page, status=status)
    return page_response(items, total, page, AdminPlatformPolicyResponse)


@router.get(
    "/platform-policies/{policy_key}/events",
    response_model=PageResponse[AdminPlatformPolicyEventResponse],
)
async def list_admin_platform_policy_events(
    policy_key: str,
    page: PageParams = Depends(pagination_params),
    event_type: str | None = Query(default=None),
    service: AdminPolicyService = Depends(admin_policy_service),
) -> PageResponse[AdminPlatformPolicyEventResponse]:
    result = service.list_platform_policy_events(policy_key, page, event_type=event_type)
    if result is None:
        raise HTTPException(status_code=404, detail="Platform policy not found")
    items, total = result
    return page_response(items, total, page, AdminPlatformPolicyEventResponse)


@router.get(
    "/platform-policies/risky-execution",
    response_model=AdminPlatformPolicyResponse,
)
async def get_admin_risky_execution_policy(
    service: AdminPolicyService = Depends(admin_policy_service),
) -> AdminPlatformPolicyResponse:
    policy = service.get_or_create_risky_execution_policy()
    return AdminPlatformPolicyResponse.model_validate(policy)


@router.patch(
    "/platform-policies/risky-execution",
    response_model=AdminPlatformPolicyResponse,
)
async def update_admin_risky_execution_policy(
    request: AdminRiskyExecutionPolicyUpdateRequest,
    service: AdminPolicyService = Depends(admin_policy_service),
) -> AdminPlatformPolicyResponse:
    policy = service.update_risky_execution_policy(
        value=request.value,
        updated_by=request.updated_by,
        description=request.description,
    )
    return AdminPlatformPolicyResponse.model_validate(policy)


@router.get(
    "/platform-policies/worker-control",
    response_model=AdminPlatformPolicyResponse,
)
async def get_admin_worker_control_policy(
    service: AdminPolicyService = Depends(admin_policy_service),
) -> AdminPlatformPolicyResponse:
    policy = service.get_or_create_worker_control_policy()
    return AdminPlatformPolicyResponse.model_validate(policy)


@router.patch(
    "/platform-policies/worker-control",
    response_model=AdminPlatformPolicyResponse,
)
async def update_admin_worker_control_policy(
    request: AdminWorkerControlPolicyUpdateRequest,
    service: AdminPolicyService = Depends(admin_policy_service),
) -> AdminPlatformPolicyResponse:
    policy = service.update_worker_control_policy(
        value=request.value,
        updated_by=request.updated_by,
        description=request.description,
    )
    return AdminPlatformPolicyResponse.model_validate(policy)
