from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.runtime.environment.spaces.lifecycle_support import RuntimeSpaceEventLog
from backend.app.runtime.environment.spaces.models import (
    RuntimeSpace,
    RuntimeSpaceQuota,
    RuntimeSpaceReservation,
)
from backend.app.runtime.environment.spaces.quotas import RuntimeSpaceQuotaCounter


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


@dataclass(frozen=True, slots=True)
class RuntimeSpaceReservationResult:
    reservation: RuntimeSpaceReservation | None
    blocked_reason: str | None = None


class RuntimeSpaceCapacityReservationService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._quota_counter = RuntimeSpaceQuotaCounter(session)
        self._events = RuntimeSpaceEventLog(session)

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


@dataclass(frozen=True, slots=True)
class RuntimeSpaceForceReleaseResult:
    released_reservations: int
    released_keys: list[str]


class RuntimeSpaceReservationReleaseService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._quota_counter = RuntimeSpaceQuotaCounter(session)
        self._events = RuntimeSpaceEventLog(session)

    def release_reservations_for_run(
        self,
        *,
        workspace_id: UUID,
        agent_run_id: UUID,
        released_at: datetime | None = None,
    ) -> int:
        reservations = self._active_reservations_for_run(
            workspace_id=workspace_id,
            agent_run_id=agent_run_id,
        )
        if not reservations:
            return 0
        runtime_spaces = self._runtime_spaces_for(reservations)
        self._release_reservations(
            reservations,
            release_time=released_at or datetime.now(UTC),
            event_message=lambda reservation: (
                f"Released runtime space capacity for run {agent_run_id}"
            ),
            event_metadata=lambda reservation: {
                "agent_run_id": str(agent_run_id),
                "reservation_key": reservation.reservation_key,
            },
            runtime_space_for=lambda reservation: runtime_spaces.get(reservation.runtime_space_id),
        )
        return len(reservations)

    def release_reservation_by_key(
        self,
        *,
        workspace_id: UUID,
        runtime_space_id: UUID,
        reservation_key: str,
        released_at: datetime | None = None,
    ) -> bool:
        reservation = self._session.scalar(
            select(RuntimeSpaceReservation)
            .where(
                RuntimeSpaceReservation.workspace_id == workspace_id,
                RuntimeSpaceReservation.runtime_space_id == runtime_space_id,
                RuntimeSpaceReservation.reservation_key == reservation_key,
                RuntimeSpaceReservation.status == "active",
            )
            .with_for_update()
        )
        if reservation is None:
            return False
        runtime_space = self._session.scalar(
            select(RuntimeSpace).where(
                RuntimeSpace.workspace_id == workspace_id,
                RuntimeSpace.id == runtime_space_id,
            )
        )
        self._release_reservations(
            [reservation],
            release_time=released_at or datetime.now(UTC),
            event_message=lambda item: (
                f"Released runtime space capacity for {item.reservation_key}"
            ),
            event_metadata=lambda item: {
                "reservation_key": item.reservation_key,
                "resource_usage": dict(reservation_usage(item)),
            },
            runtime_space_for=lambda item: runtime_space,
        )
        return True

    def force_release_active_reservations(
        self,
        *,
        runtime_space: RuntimeSpace,
        reservation_key: str | None,
    ) -> RuntimeSpaceForceReleaseResult:
        statement = (
            select(RuntimeSpaceReservation)
            .where(
                RuntimeSpaceReservation.workspace_id == runtime_space.workspace_id,
                RuntimeSpaceReservation.runtime_space_id == runtime_space.id,
                RuntimeSpaceReservation.status == "active",
            )
            .with_for_update()
        )
        if reservation_key is not None:
            statement = statement.where(
                RuntimeSpaceReservation.reservation_key == reservation_key
            )
        reservations = self._session.scalars(statement).all()
        if not reservations:
            return RuntimeSpaceForceReleaseResult(0, [])
        released_keys = [reservation.reservation_key for reservation in reservations]
        self._release_reservations(
            reservations,
            release_time=datetime.now(UTC),
            event_message=None,
            event_metadata=None,
            runtime_space_for=lambda item: runtime_space,
        )
        return RuntimeSpaceForceReleaseResult(len(reservations), released_keys)

    def _active_reservations_for_run(
        self,
        *,
        workspace_id: UUID,
        agent_run_id: UUID,
    ) -> Sequence[RuntimeSpaceReservation]:
        return self._session.scalars(
            select(RuntimeSpaceReservation)
            .where(
                RuntimeSpaceReservation.workspace_id == workspace_id,
                RuntimeSpaceReservation.agent_run_id == agent_run_id,
                RuntimeSpaceReservation.status == "active",
            )
            .with_for_update()
        ).all()

    def _release_reservations(
        self,
        reservations: Sequence[RuntimeSpaceReservation],
        *,
        release_time: datetime,
        event_message: Callable[[RuntimeSpaceReservation], str] | None,
        event_metadata: Callable[[RuntimeSpaceReservation], dict[str, object]] | None,
        runtime_space_for: Callable[[RuntimeSpaceReservation], RuntimeSpace | None],
    ) -> None:
        quotas = self._quotas_for(reservations)
        for reservation in reservations:
            for quota_key, amount in reservation_usage(reservation).items():
                quota = quotas.get((reservation.runtime_space_id, quota_key))
                if quota is not None:
                    self._quota_counter.decrement(quota, amount)
            reservation.status = "released"
            reservation.released_at = release_time
            runtime_space = runtime_space_for(reservation)
            if (
                runtime_space is not None
                and event_message is not None
                and event_metadata is not None
            ):
                self._events.append(
                    runtime_space,
                    "runtime_space.reservation_released",
                    event_message(reservation),
                    event_metadata(reservation),
                )
        self._session.flush([*reservations, *quotas.values()])

    def _quotas_for(
        self,
        reservations: Sequence[RuntimeSpaceReservation],
    ) -> dict[tuple[UUID, str], RuntimeSpaceQuota]:
        quota_keys = {
            quota_key
            for reservation in reservations
            for quota_key in reservation_usage(reservation)
        }
        if not quota_keys:
            return {}
        runtime_space_ids = {reservation.runtime_space_id for reservation in reservations}
        workspace_id = reservations[0].workspace_id
        return {
            (quota.runtime_space_id, quota.quota_key): quota
            for quota in self._session.scalars(
                select(RuntimeSpaceQuota)
                .where(
                    RuntimeSpaceQuota.workspace_id == workspace_id,
                    RuntimeSpaceQuota.runtime_space_id.in_(runtime_space_ids),
                    RuntimeSpaceQuota.quota_key.in_(quota_keys),
                )
                .with_for_update()
            ).all()
        }

    def _runtime_spaces_for(
        self,
        reservations: Sequence[RuntimeSpaceReservation],
    ) -> dict[UUID, RuntimeSpace]:
        runtime_space_ids = {reservation.runtime_space_id for reservation in reservations}
        return {
            runtime_space.id: runtime_space
            for runtime_space in self._session.scalars(
                select(RuntimeSpace).where(
                    RuntimeSpace.workspace_id == reservations[0].workspace_id,
                    RuntimeSpace.id.in_(runtime_space_ids),
                )
            ).all()
        }


RUN_CAPACITY_QUOTA_KEY = "active_runs"


def normalize_reservation_usage(resource_usage: Mapping[str, object] | None) -> dict[str, int]:
    usage = {
        key: value
        for key, value in (resource_usage or {}).items()
        if isinstance(key, str)
        and isinstance(value, int)
        and not isinstance(value, bool)
        and value > 0
    }
    return usage or {RUN_CAPACITY_QUOTA_KEY: 1}


def reservation_usage(reservation: RuntimeSpaceReservation) -> dict[str, int]:
    return normalize_reservation_usage(
        reservation.resource_usage if isinstance(reservation.resource_usage, dict) else {}
    )


def first_exceeded_quota(
    quotas: dict[str, RuntimeSpaceQuota],
    usage: dict[str, int],
) -> RuntimeSpaceQuota | None:
    for quota_key, amount in usage.items():
        quota = quotas.get(quota_key)
        if quota is None:
            continue
        if quota.reserved_value + amount > quota.limit_value:
            return quota
    return None


def active_reservation_matches(
    *,
    reservation: RuntimeSpaceReservation,
    task_id: object,
    task_step_id: object,
    resource_usage: dict[str, int],
) -> bool:
    return (
        reservation.task_id == task_id
        and reservation.task_step_id == task_step_id
        and reservation_usage(reservation) == resource_usage
    )
