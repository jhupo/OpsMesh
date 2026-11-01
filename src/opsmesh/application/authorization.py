"""Bind resource ownership and capability policy to local account grants."""

from __future__ import annotations

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import VersionedRecordStore
from opsmesh.modules.accounts.api import AccountDirectory
from opsmesh.modules.capabilities.api import CapabilityCatalog
from opsmesh.modules.models.api import ModelProfiles
from opsmesh.modules.models.validation import validate_personal_endpoint

from .ports import ApplicationSettings


class PlatformAuthorization:
    def __init__(
        self,
        *,
        accounts: AccountDirectory,
        models: ModelProfiles,
        capabilities: CapabilityCatalog,
        workspace_owners: VersionedRecordStore,
        settings: ApplicationSettings,
    ) -> None:
        self.accounts = accounts
        self.models = models
        self.capabilities = capabilities
        self.workspace_owners = workspace_owners
        self.settings = settings

    async def check(self, actor_id: str, action: str, resource: str) -> None:
        principal = await self.accounts.get_principal(actor_id)
        if action in {"model:use", "model:read"} and resource != "*":
            profile = await self.models.get_definition(resource)
            if profile.owner_id is not None:
                if not principal.active or profile.owner_id != actor_id:
                    raise OpsMeshError("FORBIDDEN", "Model belongs to another user")
                validate_personal_endpoint(profile, self.settings.personal_model_allowed_hosts)
                return
        await self.accounts.authorize(principal, action, resource)

    async def capability(self, actor_id: str, resource: str) -> None:
        await self.check(actor_id, "capability:use", resource)

    async def tool(self, actor_id: str, tool_ref: str) -> None:
        tool = await self.capabilities.get_tool(tool_ref)
        principal = await self.accounts.get_principal(actor_id)
        if not principal.active:
            raise OpsMeshError("FORBIDDEN", "User is disabled")
        if tool.code_ref.startswith("resource:") and tool.allowed_principals == (actor_id,):
            # Compiled closures recheck their originating skill/MCP/knowledge grants.
            return
        await self.accounts.authorize(principal, "tool:invoke", tool_ref)

    async def workspace(self, principal_id: str, workspace_id: str, *, write: bool = False) -> None:
        principal = await self.accounts.get_principal(principal_id)
        ownership = await self.workspace_owners.get(workspace_id)
        owner_id = ownership.data["owner_id"] if ownership else None
        await self.accounts.authorize(
            principal,
            "workspace:write" if write else "workspace:read",
            workspace_id,
            owner_id=owner_id,
        )

    async def active_actor(self, actor_id: str) -> None:
        principal = await self.accounts.get_principal(actor_id)
        if not principal.active:
            raise OpsMeshError("FORBIDDEN", "User is disabled")
