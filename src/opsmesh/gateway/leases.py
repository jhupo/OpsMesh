"""Database-clock leases fence durable writes before dispatch."""

from datetime import timedelta
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from opsmesh.gateway.models import GatewayLease
from opsmesh.gateway.store import GatewayStore
from opsmesh.shared.errors import ConflictError

LEASE_SECONDS = 45


def require_lease(session: Session, channel_id: UUID, owner: str) -> GatewayLease:
    row = session.scalar(
        select(GatewayLease)
        .where(
            GatewayLease.channel_id == channel_id,
            GatewayLease.owner == owner,
            GatewayLease.expires_at > func.now(),
        )
        .with_for_update()
    )
    if row is None:
        raise ConflictError("Gateway channel lease was lost")
    return row


class ChannelLeases:
    def __init__(self, store: GatewayStore) -> None:
        self.store = store

    def acquire(self, channel_id: UUID, owner: str) -> bool:
        with self.store.sessions.begin() as session:
            statement = insert(GatewayLease).values(
                channel_id=channel_id,
                owner=owner,
                expires_at=func.now() + timedelta(seconds=LEASE_SECONDS),
            )
            result = session.execute(
                statement.on_conflict_do_update(
                    index_elements=[GatewayLease.channel_id],
                    set_={"owner": owner, "expires_at": statement.excluded.expires_at},
                    where=(GatewayLease.expires_at <= func.now()) | (GatewayLease.owner == owner),
                ).returning(GatewayLease.channel_id)
            )
            return result.scalar_one_or_none() is not None

    def release(self, channel_id: UUID, owner: str) -> None:
        with self.store.sessions.begin() as session:
            session.execute(
                delete(GatewayLease).where(
                    GatewayLease.channel_id == channel_id, GatewayLease.owner == owner
                )
            )
