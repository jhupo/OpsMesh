"""Exact conversation routes precede private-only fallback routes."""

from uuid import UUID

from opsmesh_sdk.channels import InboundMessage
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from opsmesh.gateway.contracts import RouteDefinition
from opsmesh.gateway.models import (
    GatewayAccountBinding,
    GatewayIdentity,
    GatewayRoute,
    GatewaySession,
)
from opsmesh.gateway.store import GatewayStore
from opsmesh.shared.errors import ConflictError, ForbiddenError, NotFoundError


def route_value(row: GatewayRoute) -> RouteDefinition:
    return RouteDefinition.model_validate(
        {key: getattr(row, key) for key in RouteDefinition.model_fields}
    )


def resolve_route(
    session: Session, channel_id: UUID, identity_id: UUID, message: InboundMessage
) -> tuple[UUID, RouteDefinition]:
    binding = session.get(GatewayAccountBinding, identity_id)
    if binding is None:
        raise ForbiddenError("External identity has not been bound")
    row = session.scalar(
        select(GatewayRoute).where(
            GatewayRoute.channel_id == channel_id,
            GatewayRoute.identity_id == identity_id,
            GatewayRoute.conversation_id == message.conversation.external_id,
            GatewayRoute.enabled.is_(True),
        )
    )
    if row is None and message.conversation.kind == "private":
        row = session.scalar(
            select(GatewayRoute).where(
                GatewayRoute.identity_id == identity_id,
                GatewayRoute.conversation_id == "",
                GatewayRoute.enabled.is_(True),
            )
        )
    if row is None or (message.conversation.kind != "private" and not row.allow_group):
        raise ForbiddenError("No authorized route for this conversation")
    return binding.user_id, route_value(row)


class Routing:
    def __init__(self, store: GatewayStore) -> None:
        self.store = store

    def save(self, value: RouteDefinition, actor_id: UUID) -> RouteDefinition:
        with self.store.sessions.begin() as session:
            identity = session.get(GatewayIdentity, value.identity_id, with_for_update=True)
            if identity is None or identity.channel_id != value.channel_id:
                raise NotFoundError("External identity does not belong to this channel")
            if session.get(GatewayAccountBinding, value.identity_id) is None:
                raise ConflictError("Bind the external identity first")
            row = session.get(GatewayRoute, value.id, with_for_update=True)
            if row is None:
                row = GatewayRoute(**value.model_dump())
                session.add(row)
            else:
                if (
                    row.revision != value.revision
                    or row.identity_id != value.identity_id
                    or row.channel_id != value.channel_id
                ):
                    raise ConflictError("Route identity or revision changed")
                session.execute(delete(GatewaySession).where(GatewaySession.route_id == row.id))
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
