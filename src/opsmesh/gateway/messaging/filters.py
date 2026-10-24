"""Feature-owned preparation and invocation of deterministic ingress filters."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from opsmesh_sdk import PluginContext
from opsmesh_sdk.identity import Principal
from opsmesh_sdk.messages import MessageFilterRegistration, MessageFilterResult
from opsmesh_sdk.registration import InvocationContext, tool_invocation

from opsmesh.gateway.contracts import GatewayTurn
from opsmesh.gateway.management.contracts import OperationOrigin, resource_generation
from opsmesh.gateway.messaging.media import GatewayMedia
from opsmesh.plugins.base import PluginAdapter, PreparedResource, plugin_generation
from opsmesh.shared.errors import InvalidArgumentError
from opsmesh.shared.network import CredentialResolver
from opsmesh.users.access import CurrentUser


class MessageFilterAdapter(PluginAdapter):
    kind = "message_filter"

    def prepare(self, context: PluginContext, name: str, registration: object) -> PreparedResource:
        if not isinstance(registration, MessageFilterRegistration):
            raise TypeError("Expected a message filter registration")
        return PreparedResource(
            f"{context.manifest.id}/message_filter/{name}",
            context.manifest.id,
            self.kind,
            name,
            (context, registration),
            {
                "timeout_seconds": registration.timeout_seconds,
                "generation": plugin_generation(context),
            },
        )


@dataclass(frozen=True)
class FilterDecision:
    result: MessageFilterResult
    origin: OperationOrigin | None = None


@dataclass(frozen=True)
class MessageFilters:
    resources: Callable[[], dict[str, PreparedResource]]
    credentials: CredentialResolver
    authorize: Callable[[GatewayTurn], Awaitable[CurrentUser]]
    media_root: Path

    async def evaluate(self, turn: GatewayTurn) -> FilterDecision:
        resources = self.resources()
        actor = await self.authorize(turn)
        result = MessageFilterResult(action="allow")
        for identifier in turn.policy.message_filters:
            resource = resources.get(identifier)
            if resource is None or resource.kind != "message_filter":
                raise InvalidArgumentError("A configured message filter is unavailable")
            context, registration = resource.value
            async with asyncio.timeout(registration.timeout_seconds):
                secrets = {
                    alias: await self.credentials(reference)
                    for alias, reference in context.credential_refs.items()
                }
                with tool_invocation(
                    InvocationContext(
                        uuid5(NAMESPACE_URL, f"{turn.inbox_id}:{identifier}"),
                        actor.user_id,
                        actor.has_platform_role("admin"),
                        context.config,
                        identifier,
                        Principal(user_id=actor.user_id, roles=actor.platform_roles),
                        turn.source,
                        secrets,
                    )
                ):
                    result = MessageFilterResult.model_validate(
                        await registration.handler(
                            turn.message, GatewayMedia(self.media_root / str(turn.channel_id))
                        )
                    )
            if result.action != "allow":
                return FilterDecision(
                    result,
                    OperationOrigin(
                        kind="filter",
                        actor_id=actor.user_id,
                        resource_id=identifier,
                        generation=resource_generation(resource),
                        source_id=turn.inbox_id,
                    ),
                )
        return FilterDecision(result)
