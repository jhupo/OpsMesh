from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.runtime_manager.spaces.models import RuntimeSpaceReservation
from backend.app.runtime_manager.spaces.reservation_usage import reservation_usage


class RuntimeSpaceReservationAttachmentService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def attach_reservation_to_run(
        self,
        reservation: RuntimeSpaceReservation,
        agent_run_id: UUID,
    ) -> None:
        reservation.agent_run_id = agent_run_id
        self._session.flush([reservation])

    def active_reservation_usage_for_run(
        self,
        *,
        workspace_id: UUID,
        agent_run_id: UUID,
    ) -> dict[str, int]:
        usage: dict[str, int] = {}
        reservations = self._session.scalars(
            select(RuntimeSpaceReservation).where(
                RuntimeSpaceReservation.workspace_id == workspace_id,
                RuntimeSpaceReservation.agent_run_id == agent_run_id,
                RuntimeSpaceReservation.status == "active",
            )
        ).all()
        for reservation in reservations:
            for quota_key, amount in reservation_usage(reservation).items():
                usage[quota_key] = usage.get(quota_key, 0) + amount
        return usage
