"""Deployed plugin policy and explicit outbox recovery."""

from typing import Any

from fastapi import APIRouter, Request, Response
from pydantic import Field

from opsmesh.framework.typing import OpsMeshContract
from opsmesh.modules.operations.api import PluginDeploymentPolicy, PluginInstanceStatus

from ..security import CurrentUser, get_application

router = APIRouter(prefix="/operations", tags=["operations"])


class RequeueRequest(OpsMeshContract):
    reason: str = Field(min_length=1, max_length=1000)


class PluginPolicyRequest(OpsMeshContract):
    enabled: bool
    expected_revision: int = Field(ge=0)
    cascade: bool = False


@router.get("/outbox")
async def outbox(
    request: Request, user: CurrentUser, status: str = "dead", limit: int = 100, offset: int = 0
) -> list[dict[str, Any]]:
    return await get_application(request).services.outbox_management.list_events(
        actor_id=user.id, status=status, limit=limit, offset=offset
    )


@router.post("/outbox/{event_id}/requeue", status_code=204)
async def requeue(
    event_id: str, body: RequeueRequest, request: Request, user: CurrentUser
) -> Response:
    await get_application(request).services.outbox_management.requeue(
        event_id, actor_id=user.id, reason=body.reason
    )
    return Response(status_code=204)


@router.get("/plugins")
async def plugins(request: Request, user: CurrentUser) -> list[dict[str, Any]]:
    return await get_application(request).services.plugin_management.inventory(actor_id=user.id)


@router.get("/plugin-policy")
async def plugin_policy(request: Request, user: CurrentUser) -> PluginDeploymentPolicy:
    return await get_application(request).services.plugin_management.policy(actor_id=user.id)


@router.put("/plugins/{module}/enabled")
async def set_plugin_enabled(
    module: str, body: PluginPolicyRequest, request: Request, user: CurrentUser
) -> PluginDeploymentPolicy:
    return await get_application(request).services.plugin_management.set_enabled(
        module,
        body.enabled,
        actor_id=user.id,
        expected_revision=body.expected_revision,
        cascade=body.cascade,
    )


@router.get("/plugin-instances")
async def plugin_instances(request: Request, user: CurrentUser) -> list[PluginInstanceStatus]:
    return await get_application(request).services.plugin_management.instance_status(
        actor_id=user.id
    )
