"""Resolve a conversation from the platform's authoritative Bot-to-Agent route."""

import hashlib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.agents.management import AgentManagement
from opsmesh.modules.bots.api import BotAccounts

from .configuration import ConversationBinding, ConversationOptions, ConversationSettings
from .validation import validate_conversation_agent


class ConversationGateway(BaseModel):
    """A policy for an existing group route; Agent and workspace are never duplicated."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bot_id: str = Field(min_length=1, max_length=100)
    channel_id: str = Field(min_length=1, max_length=256)
    principal_id: str = Field(min_length=1, max_length=256)
    proactive_enabled: bool = False
    knowledge_collection: str | None = None
    settings: ConversationSettings = Field(default_factory=ConversationSettings)

    @property
    def scope(self) -> str:
        identity = f"{len(self.bot_id)}:{self.bot_id}{self.channel_id}"
        return "gateway-" + hashlib.sha256(identity.encode()).hexdigest()

    async def resolve(
        self, bots: BotAccounts, agents: AgentManagement, data_directory: Path
    ) -> ConversationOptions:
        account = await bots.get_account(self.bot_id, actor_id=self.principal_id)
        route = await bots.get_route(self.bot_id, self.channel_id, actor_id=self.principal_id)
        if (
            not account.enabled
            or account.unlinked_principal_id != self.principal_id
            or self.channel_id not in account.unlinked_channels
            or route.dispatch_mode != "managed"
        ):
            raise OpsMeshError(
                "CONFLICT", "Conversation requires an enabled, delegated group route"
            )
        agent = await agents.get_revision(route.agent_revision, actor_id=self.principal_id)
        validate_conversation_agent(agent)
        if (
            self.knowledge_collection
            and self.knowledge_collection not in agent.knowledge_collections
        ):
            raise OpsMeshError("FORBIDDEN", "Capture collection is not bound to this Agent")
        binding = ConversationBinding(
            bot_id=self.bot_id,
            channel_id=self.channel_id,
            principal_id=self.principal_id,
            agent_ref=route.agent_revision,
            workspace_id=agent.workspace_id,
            display_name=agent.name,
            proactive_enabled=self.proactive_enabled,
            knowledge_collection=self.knowledge_collection,
            memory_namespace_id=agent.memory_namespace_id,
        )
        return ConversationOptions(
            **self.settings.model_dump(),
            data_directory=data_directory / "conversations" / self.scope,
            bindings=(binding,),
        )
