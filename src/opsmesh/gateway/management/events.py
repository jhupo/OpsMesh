"""Durable non-chat ingress with versioned, bounded plugin decisions."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4, uuid5

from opsmesh_sdk import PluginContext
from opsmesh_sdk.channel_events import ConversationEvent, EventDecision, EventHandlerRegistration
from opsmesh_sdk.identity import Principal
from opsmesh_sdk.registration import InvocationContext, tool_invocation
from pydantic import TypeAdapter
from sqlalchemy import select

from opsmesh.gateway.channels.leases import require_lease
from opsmesh.gateway.contracts import ChannelDefinition
from opsmesh.gateway.management.contracts import OperationOrigin, reference_key, resource_generation
from opsmesh.gateway.management.service import Management
from opsmesh.gateway.management.worker import OriginAuthorizer
from opsmesh.gateway.storage.models import (
    GatewayChannel,
    GatewayConversation,
    GatewayConversationEvent,
)
from opsmesh.gateway.storage.store import GatewayStore
from opsmesh.plugins.base import PluginAdapter, PreparedResource, plugin_generation
from opsmesh.shared.errors import ConflictError, ForbiddenError, InvalidArgumentError, NotFoundError
from opsmesh.shared.network import CredentialResolver
from opsmesh.shared.serialization import payload_digest


class ConversationEventAdapter(PluginAdapter):
    kind = "conversation_event"

    def prepare(self, context: PluginContext, name: str, registration: object) -> PreparedResource:
        if not isinstance(registration, EventHandlerRegistration):
            raise TypeError("Expected a conversation event handler registration")
        return PreparedResource(
            f"{context.manifest.id}/conversation_event/{name}",
            context.manifest.id,
            self.kind,
            name,
            (context, registration),
            {
                "timeout_seconds": registration.timeout_seconds,
                "generation": plugin_generation(context),
            },
        )


class ConversationEvents:
    def __init__(
        self,
        store: GatewayStore,
        resources: Callable[[], dict[str, PreparedResource]],
        credentials: CredentialResolver,
        authorize: OriginAuthorizer,
        adapter_generation: Callable[[str], str],
    ) -> None:
        self.store, self.resources, self.credentials, self.authorize = (
            store,
            resources,
            credentials,
            authorize,
        )
        self.management = Management(store, adapter_generation)

    def receive(self, definition: ChannelDefinition, owner: str, event: ConversationEvent) -> None:
        with self.store.sessions.begin() as session:
            require_lease(session, definition.id, owner)
            channel = session.get(GatewayChannel, definition.id, with_for_update=True)
            if (
                channel is None
                or not channel.enabled
                or channel.deleted
                or channel.revision != definition.revision
            ):
                raise ConflictError("The channel configuration changed")
            if event.conversation.external_id not in definition.policy.groups:
                raise ForbiddenError("The conversation is not enabled")
            event_key = payload_digest(
                {
                    "id": event.event_id,
                    "conversation": event.conversation.model_dump(mode="json"),
                    "kind": event.kind,
                }
            )
            if session.scalar(
                select(GatewayConversationEvent.id).where(
                    GatewayConversationEvent.channel_id == definition.id,
                    GatewayConversationEvent.event_key == event_key,
                )
            ):
                return
            generations = {}
            resources = self.resources()
            for identifier in definition.policy.event_handlers:
                resource = resources.get(identifier)
                if resource is None or resource.kind != "conversation_event":
                    raise InvalidArgumentError("A configured event handler is unavailable")
                generations[identifier] = resource_generation(resource)
            encrypted = self.store.cipher.encrypt_payload(
                {
                    "event": event.model_dump(mode="json"),
                    "handlers": generations,
                    "adapter_generation": self.management.adapter_generation(definition.kind),
                }
            )
            key = reference_key(event.conversation)
            conversation = session.get(GatewayConversation, (definition.id, key))
            if conversation is None:
                profile = self.store.cipher.encrypt_payload(
                    {"external_id": event.conversation.external_id, "kind": event.conversation.kind}
                )
                session.add(
                    GatewayConversation(
                        channel_id=definition.id,
                        conversation_key=key,
                        external_id=event.conversation.external_id,
                        kind=event.conversation.kind,
                        encrypted_profile=profile.ciphertext,
                        key_id=profile.key_id,
                    )
                )
            session.add(
                GatewayConversationEvent(
                    id=uuid4(),
                    channel_id=definition.id,
                    event_key=event_key,
                    channel_revision=channel.revision,
                    binding_revision=channel.binding_revision,
                    status="pending",
                    attempts=0,
                    encrypted_payload=encrypted.ciphertext,
                    key_id=encrypted.key_id,
                )
            )

    def next(
        self, definition: ChannelDefinition, owner: str
    ) -> tuple[UUID, ConversationEvent, dict[str, str]] | None:
        with self.store.sessions.begin() as session:
            require_lease(session, definition.id, owner)
            channel = session.get(GatewayChannel, definition.id, with_for_update=True)
            if channel is None or channel.deleted or not channel.enabled:
                return None
            now = datetime.now(UTC)
            for row in session.scalars(
                select(GatewayConversationEvent)
                .where(
                    GatewayConversationEvent.channel_id == definition.id,
                    GatewayConversationEvent.status == "pending",
                )
                .order_by(GatewayConversationEvent.created_at)
                .limit(100)
                .with_for_update()
            ):
                if (
                    row.channel_revision != channel.revision
                    or row.binding_revision != channel.binding_revision
                ):
                    row.status, row.error_code = "cancelled", "configuration_changed"
                    continue
                if row.retry_at and row.retry_at > now:
                    continue
                payload = self.store.cipher.decrypt_payload(
                    row.encrypted_payload, key_id=row.key_id
                )
                if payload["adapter_generation"] != self.management.adapter_generation(
                    definition.kind
                ):
                    row.status, row.error_code = "cancelled", "adapter_changed"
                    continue
                value = ConversationEvent.model_validate(payload["event"])
                if not 0 <= (now - value.occurred_at).total_seconds() < 300:
                    row.status, row.error_code = "expired", "event_expired"
                    continue
                row.attempts += 1
                return (
                    row.id,
                    value,
                    TypeAdapter(dict[str, str]).validate_python(payload["handlers"]),
                )
        return None

    def finish(
        self,
        definition: ChannelDefinition,
        owner: str,
        identifier: UUID,
        decisions: list[tuple[OperationOrigin, EventDecision]] | None,
    ) -> None:
        with self.store.sessions.begin() as session:
            require_lease(session, definition.id, owner)
            channel = session.get(GatewayChannel, definition.id, with_for_update=True)
            row = session.get(GatewayConversationEvent, identifier, with_for_update=True)
            if (
                channel is None
                or row is None
                or row.status != "pending"
                or row.channel_revision != channel.revision
            ):
                raise ConflictError("The event admission changed")
            if decisions is None:
                row.status = "failed" if row.attempts >= 3 else "pending"
                row.error_code = "handler_failed"
                row.retry_at = datetime.now(UTC) + timedelta(seconds=10)
                return
            payload = self.store.cipher.decrypt_payload(row.encrypted_payload, key_id=row.key_id)
            if payload["adapter_generation"] != self.management.adapter_generation(definition.kind):
                raise ConflictError("The event channel adapter changed")
            for origin, decision in decisions:
                resource = self.resources().get(origin.resource_id or "")
                if resource is None or resource_generation(resource) != origin.generation:
                    raise ConflictError("The event handler changed")
                for index, operation in enumerate(decision.operations):
                    self.management.submit_in(
                        session,
                        definition.id,
                        uuid5(row.id, f"{origin.resource_id}:{index}"),
                        origin,
                        operation,
                        on_denied="record",
                    )
            row.status, row.error_code = "completed", None

    def list(self, channel_id: UUID) -> list[dict[str, object]]:
        with self.store.sessions() as session:
            return [
                {
                    "id": str(row.id),
                    "event": self.store.cipher.decrypt_payload(
                        row.encrypted_payload, key_id=row.key_id
                    )["event"],
                    "status": row.status,
                    "attempts": row.attempts,
                    "error_code": row.error_code,
                }
                for row in session.scalars(
                    select(GatewayConversationEvent)
                    .where(GatewayConversationEvent.channel_id == channel_id)
                    .order_by(GatewayConversationEvent.created_at.desc())
                    .limit(100)
                )
            ]

    def cancel(self, channel_id: UUID, identifier: UUID, actor_id: UUID) -> None:
        with self.store.sessions.begin() as session:
            channel = session.get(GatewayChannel, channel_id, with_for_update=True)
            row = session.get(GatewayConversationEvent, identifier, with_for_update=True)
            if channel is None or channel.deleted or row is None or row.channel_id != channel_id:
                raise NotFoundError("Conversation event does not exist")
            if row.status not in {"pending", "failed"}:
                raise ConflictError("The event cannot be cancelled")
            row.status = "cancelled"
            self.store.audit(session, "event.cancel", actor_id, row.id)

    async def run(self, definition: ChannelDefinition, owner: str) -> None:
        while True:
            item = await asyncio.to_thread(self.next, definition, owner)
            if item is None:
                await asyncio.sleep(1)
                continue
            identifier, event, generations = item
            decisions = []
            try:
                assert definition.target is not None
                for resource_id, generation in generations.items():
                    resource = self.resources().get(resource_id)
                    if resource is None or resource_generation(resource) != generation:
                        raise ConflictError("The event handler generation changed")
                    origin = OperationOrigin(
                        kind="event",
                        actor_id=definition.target.user_id,
                        resource_id=resource_id,
                        generation=generation,
                        source_id=identifier,
                    )
                    await self.authorize(origin, definition)
                    context, registration = resource.value
                    async with asyncio.timeout(registration.timeout_seconds):
                        secrets = {
                            alias: await self.credentials(reference)
                            for alias, reference in context.credential_refs.items()
                        }
                        with tool_invocation(
                            InvocationContext(
                                uuid5(identifier, resource_id),
                                origin.actor_id,
                                False,
                                context.config,
                                resource_id,
                                Principal(user_id=origin.actor_id),
                                credentials=secrets,
                            )
                        ):
                            decision = EventDecision.model_validate(
                                await registration.handler(event)
                            )
                    decisions.append((origin, decision))
                await asyncio.to_thread(self.finish, definition, owner, identifier, decisions)
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.to_thread(self.finish, definition, owner, identifier, None)
