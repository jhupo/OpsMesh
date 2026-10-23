"""Exact conversation routes precede private-only fallback routes."""

from uuid import UUID

from opsmesh_sdk.channels import InboundMessage
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.gateway.channels.leases import require_lease
from opsmesh.gateway.contracts import GatewayTurn, RouteDefinition
from opsmesh.gateway.identity.conversations import conversation_key
from opsmesh.gateway.storage.models import (
    GatewayAccountBinding,
    GatewayChannel,
    GatewayConversation,
    GatewayIdentity,
    GatewayRoute,
)
from opsmesh.gateway.storage.store import GatewayStore
from opsmesh.shared.errors import ConflictError, ForbiddenError, NotFoundError


def authorize_turn(session: Session, turn: GatewayTurn) -> None:
    require_lease(session, turn.channel_id, turn.lease_owner)
    channel = session.get(GatewayChannel, turn.channel_id)
    if channel is None or not channel.enabled:
        raise ConflictError("Gateway channel is disabled")
    user_id, route = resolve_route(session, turn.channel_id, turn.identity_id, turn.message)
    if user_id != turn.user_id or route != turn.route:
        raise ConflictError("Gateway account binding or route changed")
    for item in turn.batch:
        user_id, route = resolve_route(session, turn.channel_id, item.identity_id, item.message)
        if user_id != turn.user_id or route != item.route:
            raise ConflictError("A batched message account binding or route changed")
        if (route.target_kind, route.target_id, route.output_mode) != (
            turn.route.target_kind,
            turn.route.target_id,
            turn.route.output_mode,
        ):
            raise ConflictError("Batched messages must have the same authorized destination")


def route_value(row: GatewayRoute) -> RouteDefinition:
    return RouteDefinition.model_validate(
        {key: getattr(row, key) for key in RouteDefinition.model_fields}
    )


def resolve_route(
    session: Session, channel_id: UUID, identity_id: UUID, message: InboundMessage
) -> tuple[UUID, RouteDefinition]:
    if message.conversation.kind != "private":
        shared = session.scalar(
            select(GatewayRoute).where(
                GatewayRoute.channel_id == channel_id,
                GatewayRoute.conversation_key == conversation_key(message),
            )
        )
        if shared is not None:
            if not shared.enabled or not shared.allow_group or shared.user_id is None:
                raise ForbiddenError("The shared conversation route is disabled")
            return shared.user_id, route_value(shared)
    binding = session.get(GatewayAccountBinding, identity_id)
    if binding is None:
        raise ForbiddenError("External identity has not been bound")
    row = session.scalar(
        select(GatewayRoute).where(
            GatewayRoute.channel_id == channel_id,
            GatewayRoute.identity_id == identity_id,
            GatewayRoute.conversation_id == message.conversation.external_id,
        )
    )
    if row is None and message.conversation.kind == "private":
        row = session.scalar(
            select(GatewayRoute).where(
                GatewayRoute.channel_id == channel_id,
                GatewayRoute.identity_id == identity_id,
                GatewayRoute.conversation_id == "",
            )
        )
    if (
        row is None
        or not row.enabled
        or (message.conversation.kind != "private" and not row.allow_group)
    ):
        raise ForbiddenError("No authorized route for this conversation")
    return binding.user_id, route_value(row)


def route_principal(session: Session, value: RouteDefinition) -> UUID:
    if value.identity_id is None:
        conversation = session.get(GatewayConversation, (value.channel_id, value.conversation_key))
        if conversation is None or conversation.kind == "private":
            raise NotFoundError("Shared routes require an observed group or guild conversation")
        assert value.user_id is not None
        return value.user_id
    identity = session.get(GatewayIdentity, value.identity_id)
    if identity is None or identity.channel_id != value.channel_id:
        raise NotFoundError("External identity does not belong to this channel")
    binding = session.get(GatewayAccountBinding, value.identity_id)
    if binding is None:
        raise ConflictError("Bind the external identity first")
    return binding.user_id


class Routing:
    def __init__(self, store: GatewayStore) -> None:
        self.store = store

    def save(self, value: RouteDefinition, actor_id: UUID) -> RouteDefinition:
        with self.store.sessions.begin() as session:
            route_principal(session, value)
            row = session.get(GatewayRoute, value.id, with_for_update=True)
            if row is None:
                row = GatewayRoute(**value.model_dump())
                session.add(row)
            else:
                if (
                    row.revision != value.revision
                    or row.identity_id != value.identity_id
                    or row.channel_id != value.channel_id
                    or row.conversation_key != value.conversation_key
                ):
                    raise ConflictError("Route identity or revision changed")
                for name, item in value.model_dump(exclude={"id", "revision"}).items():
                    setattr(row, name, item)
                row.revision += 1
            session.flush()
            self.store.audit(session, "route.save", actor_id, row.id)
            return route_value(row)

    def list(self, channel_id: UUID) -> list[RouteDefinition]:
        with self.store.sessions() as session:
            return [
                route_value(row)
                for row in session.scalars(
                    select(GatewayRoute).where(GatewayRoute.channel_id == channel_id)
                )
            ]
