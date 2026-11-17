"""Example-owned HTTP identity policy, separate from channel transport."""

import hmac
import os
from typing import Annotated

from pydantic import BaseModel, Field, InstanceOf
from starlette.requests import Request

from opsmesh.params import Context
from opsmesh.plugins import get_current_plugin
from opsmesh.plugins.channels import UserContext
from opsmesh.plugins.channels.api import HTTPError


class HTTPIdentitySettings(BaseModel):
    """Configure the example's single HTTP caller."""

    http_token_ref: str = "OPSMESH_API_TOKEN"
    http_user_id: str = Field(default="operator", min_length=1)


async def caller(
    request: Annotated[InstanceOf[Request], Context()],
) -> UserContext:
    """Resolve one example caller using its consumer-owned credential."""
    owner = get_current_plugin()
    settings = HTTPIdentitySettings.model_validate(owner.config_values)
    expected = os.environ.get(settings.http_token_ref, "")
    if not expected:
        raise HTTPError(503, "HTTP identity is not configured")
    supplied = request.headers.get("authorization", "")
    if not hmac.compare_digest(
        supplied.encode(), f"Bearer {expected}".encode()
    ):
        raise HTTPError(401, "authentication required")
    return UserContext(id=settings.http_user_id)
