"""Cross-run approval inbox filtered by current review permissions."""

from fastapi import APIRouter, Request

from opsmesh.modules.runtime.api import RunApprovalItem

from ..security import CurrentUser, get_application

router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.get("")
async def approvals(
    request: Request,
    user: CurrentUser,
    include_decided: bool = False,
) -> list[RunApprovalItem]:
    return await get_application(request).services.run_management.approval_queue(
        actor_id=user.id, include_decided=include_decided
    )
