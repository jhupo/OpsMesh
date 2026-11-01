"""Versioned configuration, audit queries and bounded operational metrics."""

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import Field

from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.typing import OpsMeshContract
from opsmesh.modules.audit.api import AuditRecord
from opsmesh.modules.configuration.api import ConfigurationEntry

from ..security import CurrentUser, authorize, get_application, reject_secret_values

router = APIRouter(tags=["management"])


class SetConfigurationRequest(OpsMeshContract):
    value: dict[str, Any]
    expected_revision: int | None = Field(default=None, ge=1)


@router.get("/configuration")
async def configurations(request: Request, user: CurrentUser) -> list[ConfigurationEntry]:
    await authorize(request, user, "configuration:read", "*")
    return await get_application(request).services.configuration_management.list(actor_id=user.id)


@router.get("/configuration/{key}")
async def configuration(key: str, request: Request, user: CurrentUser) -> ConfigurationEntry:
    await authorize(request, user, "configuration:read", key)
    entry = await get_application(request).services.configuration_management.get(
        key, actor_id=user.id
    )
    if entry is None:
        raise OpsMeshError("NOT_FOUND", "Configuration entry does not exist")
    return entry


@router.put("/configuration/{key}")
async def set_configuration(
    key: str, body: SetConfigurationRequest, request: Request, user: CurrentUser
) -> ConfigurationEntry:
    await authorize(request, user, "configuration:manage", key)
    reject_secret_values(body.value)
    return await get_application(request).services.configuration_management.set(
        key, body.value, body.expected_revision, actor_id=user.id
    )


@router.get("/audit")
async def audit(
    request: Request,
    user: CurrentUser,
    actor_id: str | None = None,
    scope: Literal["mine", "all", "system"] = "mine",
    action: str | None = None,
    resource: str | None = None,
    outcome: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[AuditRecord]:
    return await get_application(request).services.audit_management.query(
        actor_id=user.id,
        scope=scope,
        subject_id=actor_id,
        action=action,
        resource=resource,
        outcome=outcome,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics(request: Request, user: CurrentUser) -> PlainTextResponse:
    await authorize(request, user, "metrics:read", "*")
    return PlainTextResponse(
        await get_application(request).services.metric_store.render(),
        media_type="text/plain; version=0.0.4",
    )
