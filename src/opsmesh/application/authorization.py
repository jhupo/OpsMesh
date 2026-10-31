"""Bind resource ownership and capability policy to local account grants."""

from __future__ import annotations

from typing import TYPE_CHECKING

from opsmesh.framework.errors import OpsMeshError

if TYPE_CHECKING:
    from .ports import ApplicationServices


class PlatformAuthorization:
    def __init__(self, services: ApplicationServices) -> None:
        self.services = services

    async def check(self, actor_id: str, action: str, resource: str) -> None:
        principal = await self.services.accounts.get_principal(actor_id)
        await self.services.accounts.authorize(principal, action, resource)

    async def capability(self, actor_id: str, resource: str) -> None:
        await self.check(actor_id, "capability:use", resource)

    async def tool(self, actor_id: str, tool_ref: str) -> None:
        tool = await self.services.capabilities.get_tool(tool_ref)
        principal = await self.services.accounts.get_principal(actor_id)
        if not principal.active:
            raise OpsMeshError("FORBIDDEN", "User is disabled")
        if tool.code_ref.startswith("resource:") and tool.allowed_principals == (actor_id,):
            # Compiled closures recheck their originating skill/MCP/knowledge grants.
            return
        await self.services.accounts.authorize(principal, "tool:invoke", tool_ref)

    async def workspace(self, principal_id: str, workspace_id: str, *, write: bool = False) -> None:
        principal = await self.services.accounts.get_principal(principal_id)
        ownership = await self.services.workspace_owners.get(workspace_id)
        owner_id = ownership.data["owner_id"] if ownership else None
        await self.services.accounts.authorize(
            principal,
            "workspace:write" if write else "workspace:read",
            workspace_id,
            owner_id=owner_id,
        )

    async def active_actor(self, actor_id: str) -> None:
        principal = await self.services.accounts.get_principal(actor_id)
        if not principal.active:
            raise OpsMeshError("FORBIDDEN", "User is disabled")
