"""Caller-owned identity functions for transport tests."""

import os
from typing import Annotated

from pydantic import InstanceOf
from starlette.requests import Request

from opsmesh.params import Context
from opsmesh.plugins.channels import UserContext
from opsmesh.plugins.channels.api import HTTPError


async def caller(request: Annotated[InstanceOf[Request], Context()]):
    token = request.headers.get("authorization")
    if token == f"Bearer {os.getenv('OTHER_API_TOKEN', 'other')}":
        return UserContext("api:bob")
    if token != "Bearer secret":
        raise HTTPError(401, "authentication required")
    identifier = "api:alice" if os.getenv("OTHER_API_TOKEN") else "api:user"
    return UserContext(
        identifier, groups=frozenset({"support"}), roles=frozenset({"agent"})
    )


async def protocol_caller(request: Annotated[InstanceOf[Request], Context()]):
    token = request.headers.get("authorization")
    if token not in {"Bearer secret", "Bearer other"}:
        raise HTTPError(401, "authentication required")
    return UserContext("api:alice" if token == "Bearer secret" else "api:bob")


async def denied_caller(
    request: Annotated[InstanceOf[Request], Context()],
) -> UserContext:
    await caller(request)
    raise HTTPError(403, "request is not permitted")
