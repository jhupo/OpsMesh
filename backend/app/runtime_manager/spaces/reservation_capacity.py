from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.runtime_manager.spaces.models import (
    RuntimeSpace,
    RuntimeSpaceQuota,
    RuntimeSpaceReservation,
)
from backend.app.runtime_manager.spaces.reservation_events import RuntimeSpaceReservationEventLog
from backend.app.runtime_manager.spaces.reservation_quota_counter import RuntimeSpaceQuotaCounter
from backend.app.runtime_manager.spaces.reservation_usage import (
    active_reservation_matches,
    first_exceeded_quota,
    normalize_reservation_usage,
)


@dataclass(frozen=True)
class RuntimeSpaceReservationResult:
    reservation: RuntimeSpaceReservation | None
    blocked_reason: str | None = None


class RuntimeSpaceCapacityReservationService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._quota_counter = RuntimeSpaceQuotaCounter(session)
        self._events = RuntimeSpaceReservationEventLog(session)

    def reserve_run_capacity(
        self,
        *,
        workspace_id: UUID,
        runtime_space_id: UUID,
        task_id: UUID | None,
        task_step_id: UUID | None,
        reservation_key: str,
        resource_usage: dict[str, int] | None = None,
    ) -> RuntimeSpaceReservationResult:
        usage = normalize_reservation_usage(resource_usage)
        runtime_space = self._locked_runtime_space(workspace_id, runtime_space_id)
        if runtime_space is None or runtime_space.status != "active":
            return RuntimeSpaceReservationResult(
                reservation=None,
                blocked_reason="runtime_space_unavailable",
            )
        reservation = self._locked_reservation(workspace_id, runtime_space_id, reservation_key)
        if reservation is not None and reservation.status == "active":
            if not active_reservation_matches(
                reservation=reservation,
                task_id=task_id,
                task_step_id=task_step_id,
                resource_usage=usage,
            ):
                return RuntimeSpaceReservationResult(
                    reservation=None,
                    blocked_reason="runtime_space_reservation_conflict",
                )
            return RuntimeSpaceReservationResult(reservation=reservation)

        quotas = self._locked_quotas(workspace_id, runtime_space_id, usage)
        exceeded_quota = first_exceeded_quota(quotas, usage)
        if exceeded_quota is not None:
            return RuntimeSpaceReservationResult(
                reservation=None,
                blocked_reason=f"runtime_space_quota_exceeded:{exceeded_quota.quota_key}",
            )
        incremented: list[tuple[RuntimeSpaceQuota, int]] = []
        for quota_key, amount in usage.items():
            quota = quotas.get(quota_key)
            if quota is None:
                continue
            if not self._quota_counter.try_increment(quota, amount):
                self._quota_counter.rollback_increments(incremented)
                return RuntimeSpaceReservationResult(
                    reservation=None,
                    blocked_reason=f"runtime_space_quota_exceeded:{quota.quota_key}",
                )
            incremented.append((quota, amount))

        reservation = self._activate_reservation(
            reservation=reservation,
            workspace_id=workspace_id,
            runtime_space_id=runtime_space_id,
            task_id=task_id,
            task_step_id=task_step_id,
            reservation_key=reservation_key,
            usage=usage,
        )
        self._events.append(
            runtime_space,
            "runtime_space.reserved",
            f"Reserved runtime space capacity for {reservation_key}",
            {
                "reservation_key": reservation_key,
                "resource_usage": dict(usage),
            },
        )
        self._session.flush([reservation, *quotas.values()])
        return RuntimeSpaceReservationResult(reservation=reservation)

    def _locked_runtime_space(
        self,
        workspace_id: UUID,
        runtime_space_id: UUID,
    ) -> RuntimeSpace | None:
        return self._session.scalar(
            select(RuntimeSpace)
            .where(
                RuntimeSpace.workspace_id == workspace_id,
                RuntimeSpace.id == runtime_space_id,
            )
            .with_for_update()
        )

    def _locked_reservation(
        self,
        workspace_id: UUID,
        runtime_space_id: UUID,
        reservation_key: str,
    ) -> RuntimeSpaceReservation | None:
        return self._session.scalar(
            select(RuntimeSpaceReservation)
            .where(
                RuntimeSpaceReservation.workspace_id == workspace_id,
                RuntimeSpaceReservation.runtime_space_id == runtime_space_id,
                RuntimeSpaceReservation.reservation_key == reservation_key,
            )
            .with_for_update()
        )

    def _locked_quotas(
        self,
        workspace_id: UUID,
        runtime_space_id: UUID,
        usage: dict[str, int],
    ) -> dict[str, RuntimeSpaceQuota]:
        return {
            quota.quota_key: quota
            for quota in self._session.scalars(
                select(RuntimeSpaceQuota)
                .where(
                    RuntimeSpaceQuota.workspace_id == workspace_id,
                    RuntimeSpaceQuota.runtime_space_id == runtime_space_id,
                    RuntimeSpaceQuota.status == "active",
                    RuntimeSpaceQuota.quota_key.in_(usage),
                )
                .with_for_update()
            ).all()
        }

    def _activate_reservation(
        self,
        *,
        reservation: RuntimeSpaceReservation | None,
        workspace_id: UUID,
        runtime_space_id: UUID,
        task_id: UUID | None,
        task_step_id: UUID | None,
        reservation_key: str,
        usage: dict[str, int],
    ) -> RuntimeSpaceReservation:
        if reservation is None:
            reservation = RuntimeSpaceReservation(
                workspace_id=workspace_id,
                runtime_space_id=runtime_space_id,
                task_id=task_id,
                task_step_id=task_step_id,
                reservation_key=reservation_key,
                resource_usage=dict(usage),
            )
            self._session.add(reservation)
            return reservation
        reservation.task_id = task_id
        reservation.task_step_id = task_step_id
        reservation.agent_run_id = None
        reservation.resource_usage = dict(usage)
        reservation.status = "active"
        reservation.released_at = None
        reservation.expires_at = None
        return reservation
