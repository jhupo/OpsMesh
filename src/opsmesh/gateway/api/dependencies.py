"""Gateway API service lookup and platform administration authorization."""

import asyncio
from typing import Any
from uuid import UUID

from fastapi import HTTPException, Request

from opsmesh.gateway.channels.manager import Gateway
from opsmesh.users.access import require_platform_admin


def gateway(request: Request) -> Gateway:
    value = getattr(request.app.state, "gateway", None)
    if not isinstance(value, Gateway):
        raise HTTPException(503, "Gateway is unavailable")
    return value


async def managed(request: Request, user: Any, channel_id: UUID) -> Gateway:
    require_platform_admin(user)
    value = gateway(request)
    await asyncio.to_thread(value.store.get, channel_id)
    return value
