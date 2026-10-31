"""Protected resource administration routes."""

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import Field

from opsmesh.framework.typing import OpsMeshContract
from opsmesh.modules.capabilities.management import CapabilityDefinition
from opsmesh.modules.resource_contracts import ResourceReference, VersionAvailability

from ..security import CurrentUser, get_application

router = APIRouter(tags=["capabilities"])


class CapabilityAvailabilityRequest(OpsMeshContract):
    enabled: bool
    expected_revision: int = Field(ge=0)
    reason: str = Field(default="", max_length=2000)


@router.get("/capability-versions/{kind}")
async def capability_versions(
    kind: Literal["tool", "skill", "mcp", "script"], request: Request, user: CurrentUser
) -> tuple[CapabilityDefinition, ...]:
    return await get_application(request).services.capability_management.list(
        kind, actor_id=user.id
    )


@router.get("/capability-versions/{kind}/{reference}/availability")
async def capability_availability(
    kind: Literal["tool", "skill", "mcp", "script"],
    reference: str,
    request: Request,
    user: CurrentUser,
) -> VersionAvailability:
    return await get_application(request).services.capability_management.availability(
        kind, reference, actor_id=user.id
    )


@router.put("/capability-versions/{kind}/{reference}/availability")
async def set_capability_availability(
    kind: Literal["tool", "skill", "mcp", "script"],
    reference: str,
    body: CapabilityAvailabilityRequest,
    request: Request,
    user: CurrentUser,
) -> VersionAvailability:
    return await get_application(request).services.capability_management.set_enabled(
        kind,
        reference,
        body.enabled,
        actor_id=user.id,
        expected_revision=body.expected_revision,
        reason=body.reason,
    )


@router.get("/capability-versions/{kind}/{reference}")
async def capability_definition(
    kind: Literal["tool", "skill", "mcp", "script"],
    reference: str,
    request: Request,
    user: CurrentUser,
) -> CapabilityDefinition:
    return await get_application(request).services.capability_management.get(
        kind, reference, actor_id=user.id
    )


@router.get("/capability-versions/{kind}/{reference}/references")
async def capability_references(
    kind: Literal["tool", "skill", "mcp", "script"],
    reference: str,
    request: Request,
    user: CurrentUser,
) -> tuple[ResourceReference, ...]:
    return await get_application(request).services.capability_management.references(
        kind, reference, actor_id=user.id
    )
