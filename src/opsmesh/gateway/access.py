"""Revalidate persisted authority within the transaction that submits an effect."""

from sqlalchemy.orm import Session

from opsmesh.gateway.contracts import GatewayTurn
from opsmesh.gateway.leases import require_lease
from opsmesh.gateway.models import GatewayChannel
from opsmesh.gateway.routing import resolve_route
from opsmesh.shared.errors import ConflictError


def authorize_turn(session: Session, turn: GatewayTurn) -> None:
    require_lease(session, turn.channel_id, turn.lease_owner)
    channel = session.get(GatewayChannel, turn.channel_id)
    if channel is None or not channel.enabled:
        raise ConflictError("Gateway channel is disabled")
    user_id, route = resolve_route(session, turn.channel_id, turn.identity_id, turn.message)
    if user_id != turn.user_id or route != turn.route:
        raise ConflictError("Gateway account binding or route changed")
