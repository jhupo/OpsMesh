"""Authorized conversations shared by HTTP and plugin dependencies."""

import builtins
from typing import Protocol

from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.accounts.api import AccountDirectory
from opsmesh.modules.agents.api import AgentDefinitionCatalog
from opsmesh.modules.audit.api import AuditJournal
from opsmesh.modules.audit.operations import audited_operation

from .api import ConversationMessage, Conversations, ConversationSession


class WorkspaceAuthorization(Protocol):
    async def __call__(
        self, principal_id: str, workspace_id: str, *, write: bool = False
    ) -> None: ...


class ConversationManagement:
    def __init__(
        self,
        sessions: Conversations,
        accounts: AccountDirectory,
        agents: AgentDefinitionCatalog,
        authorize_workspace: WorkspaceAuthorization,
        audit: AuditJournal,
    ) -> None:
        self.sessions, self.accounts, self.agents = sessions, accounts, agents
        self.authorize_workspace, self.audit = authorize_workspace, audit

    async def _authorize(self, session_id: str, actor_id: str, action: str) -> ConversationSession:
        session = await self.sessions.get(session_id)
        actor = await self.accounts.get_principal(actor_id)
        await self.accounts.authorize(actor, action, session_id, owner_id=session.owner_id)
        return session

    async def open(
        self, agent_ref: str, workspace_id: str | None = None, *, actor_id: str
    ) -> ConversationSession:
        actor = await self.accounts.get_principal(actor_id)
        agent = await self.agents.get_revision(agent_ref)
        await self.accounts.authorize(actor, "agent:run", agent.ref)
        if workspace_id:
            await self.authorize_workspace(actor_id, workspace_id, write=True)
        async with audited_operation(self.audit, "session.open", agent.ref, actor_id):
            return await self.sessions.open(actor_id, agent.ref, workspace_id)

    async def get(self, session_id: str, *, actor_id: str) -> ConversationSession:
        return await self._authorize(session_id, actor_id, "session:read")

    async def get_or_open(
        self,
        key: str,
        agent_ref: str,
        workspace_id: str | None = None,
        *,
        actor_id: str,
    ) -> ConversationSession:
        """Resolve a principal-scoped durable session without an external ID mapping."""
        if not isinstance(key, str) or not 1 <= len(key) <= 1024:
            raise OpsMeshError("INVALID_REQUEST", "Conversation key is invalid")
        actor = await self.accounts.get_principal(actor_id)
        agent = await self.agents.get_revision(agent_ref)
        await self.accounts.authorize(actor, "agent:run", agent.ref)
        if workspace_id:
            await self.authorize_workspace(actor_id, workspace_id, write=True)
        async with audited_operation(self.audit, "session.open", agent.ref, actor_id):
            session = await self.sessions.get_or_open(key, actor_id, agent.ref, workspace_id)
        await self._authorize(session.id, actor_id, "session:read")
        if session.agent_ref != agent.ref or session.workspace_id != workspace_id:
            raise OpsMeshError("CONFLICT", "Conversation key belongs to another configuration")
        if session.status != "open":
            raise OpsMeshError("SESSION_CLOSED", "Conversation is closed")
        return session

    async def list(
        self, *, actor_id: str, owner_id: str | None = None
    ) -> list[ConversationSession]:
        actor = await self.accounts.get_principal(actor_id)
        if not actor.active:
            raise OpsMeshError("FORBIDDEN", "User is disabled")
        result = []
        for session in await self.sessions.list(owner_id):
            try:
                await self._authorize(session.id, actor_id, "session:read")
            except OpsMeshError as error:
                if error.code != "FORBIDDEN":
                    raise
            else:
                result.append(session)
        return result

    async def close(self, session_id: str, *, actor_id: str) -> ConversationSession:
        await self._authorize(session_id, actor_id, "session:write")
        async with audited_operation(self.audit, "session.close", session_id, actor_id):
            return await self.sessions.close(session_id)

    async def history(
        self, session_id: str, *, actor_id: str
    ) -> builtins.list[ConversationMessage]:
        await self._authorize(session_id, actor_id, "session:read")
        return await self.sessions.history(session_id)

    async def append_user(
        self, session_id: str, text: str, *, actor_id: str, message_id: str | None = None
    ) -> ConversationMessage:
        session = await self._authorize(session_id, actor_id, "session:write")
        if session.status != "open":
            raise OpsMeshError("SESSION_CLOSED", "Conversation is closed")
        async with audited_operation(self.audit, "session.message.append", session_id, actor_id):
            return await self.sessions.append(session_id, "user", text, message_id=message_id)
