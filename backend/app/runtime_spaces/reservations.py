from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.app.runtime_spaces.models import (
    RuntimeSpace,
    RuntimeSpaceEvent,
    RuntimeSpaceQuota,
    RuntimeSpaceReservation,
)

RUN_CAPACITY_QUOTA_KEY = "active_runs"


@dataclass(frozen=True)
class RuntimeSpaceReservationResult:
    reservation: RuntimeSpaceReservation | None
    blocked_reason: str | None = None


@dataclass(frozen=True)
class RuntimeSpaceForceReleaseResult:
    released_reservations: int
    released_keys: list[str]


class RuntimeSpaceReservationService:
    def __init__(self, session: Session) -> None:
        self._session = session

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
        runtime_space = self._session.scalar(
            select(RuntimeSpace)
            .where(
                RuntimeSpace.workspace_id == workspace_id,
                RuntimeSpace.id == runtime_space_id,
            )
            .with_for_update()
        )
        if runtime_space is None or runtime_space.status != "active":
            return RuntimeSpaceReservationResult(
                reservation=None,
                blocked_reason="runtime_space_unavailable",
            )

        reservation = self._session.scalar(
            select(RuntimeSpaceReservation)
            .where(
                RuntimeSpaceReservation.workspace_id == workspace_id,
                RuntimeSpaceReservation.runtime_space_id == runtime_space_id,
                RuntimeSpaceReservation.reservation_key == reservation_key,
            )
            .with_for_update()
        )
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

        quotas = {
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
            if not self._try_increment_quota(quota, amount):
                self._rollback_quota_increments(incremented)
                return RuntimeSpaceReservationResult(
                    reservation=None,
                    blocked_reason=f"runtime_space_quota_exceeded:{quota.quota_key}",
                )
            incremented.append((quota, amount))

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
        else:
            reservation.task_id = task_id
            reservation.task_step_id = task_step_id
            reservation.agent_run_id = None
            reservation.resource_usage = dict(usage)
            reservation.status = "active"
            reservation.released_at = None
            reservation.expires_at = None

        self._append_event(
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

    def release_reservations_for_run(
        self,
        *,
        workspace_id: UUID,
        agent_run_id: UUID,
        released_at: datetime | None = None,
    ) -> int:
        release_time = released_at or datetime.now(UTC)
        reservations = self._session.scalars(
            select(RuntimeSpaceReservation)
            .where(
                RuntimeSpaceReservation.workspace_id == workspace_id,
                RuntimeSpaceReservation.agent_run_id == agent_run_id,
                RuntimeSpaceReservation.status == "active",
            )
            .with_for_update()
        ).all()
        if not reservations:
            return 0

        quota_keys = {
            quota_key
            for reservation in reservations
            for quota_key in reservation_usage(reservation)
        }
        quotas = {
            (quota.runtime_space_id, quota.quota_key): quota
            for quota in self._session.scalars(
                select(RuntimeSpaceQuota)
                .where(
                    RuntimeSpaceQuota.workspace_id == workspace_id,
                    RuntimeSpaceQuota.runtime_space_id.in_(
                        {reservation.runtime_space_id for reservation in reservations}
                    ),
                    RuntimeSpaceQuota.quota_key.in_(quota_keys),
                )
                .with_for_update()
            ).all()
        }

        runtime_space_ids = {reservation.runtime_space_id for reservation in reservations}
        runtime_spaces = {
            runtime_space.id: runtime_space
            for runtime_space in self._session.scalars(
                select(RuntimeSpace).where(RuntimeSpace.id.in_(runtime_space_ids))
            ).all()
        }
        for reservation in reservations:
            for quota_key, amount in reservation_usage(reservation).items():
                quota = quotas.get((reservation.runtime_space_id, quota_key))
                if quota is not None:
                    quota.reserved_value = max(0, quota.reserved_value - amount)
            reservation.status = "released"
            reservation.released_at = release_time
            runtime_space = runtime_spaces.get(reservation.runtime_space_id)
            if runtime_space is not None:
                self._append_event(
                    runtime_space,
                    "runtime_space.reservation_released",
                    f"Released runtime space capacity for run {agent_run_id}",
                    {
                        "agent_run_id": str(agent_run_id),
                        "reservation_key": reservation.reservation_key,
                    },
                )
        self._session.flush([*reservations, *quotas.values()])
        return len(reservations)

    def release_reservation_by_key(
        self,
        *,
        workspace_id: UUID,
        runtime_space_id: UUID,
        reservation_key: str,
        released_at: datetime | None = None,
    ) -> bool:
        release_time = released_at or datetime.now(UTC)
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
        usage = reservation_usage(reservation)
        quotas = {
            quota.quota_key: quota
            for quota in self._session.scalars(
                select(RuntimeSpaceQuota)
                .where(
                    RuntimeSpaceQuota.workspace_id == workspace_id,
                    RuntimeSpaceQuota.runtime_space_id == runtime_space_id,
                    RuntimeSpaceQuota.quota_key.in_(usage),
                )
                .with_for_update()
            ).all()
        }
        for quota_key, amount in usage.items():
            quota = quotas.get(quota_key)
            if quota is not None:
                quota.reserved_value = max(0, quota.reserved_value - amount)
        reservation.status = "released"
        reservation.released_at = release_time
        runtime_space = self._session.scalar(
            select(RuntimeSpace).where(
                RuntimeSpace.workspace_id == workspace_id,
                RuntimeSpace.id == runtime_space_id,
            )
        )
        if runtime_space is not None:
            self._append_event(
                runtime_space,
                "runtime_space.reservation_released",
                f"Released runtime space capacity for {reservation_key}",
                {
                    "reservation_key": reservation_key,
                    "resource_usage": dict(usage),
                },
            )
        self._session.flush([reservation, *quotas.values()])
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
        quota_keys = {
            quota_key
            for reservation in reservations
            for quota_key in reservation_usage(reservation)
        }
        quotas = {
            quota.quota_key: quota
            for quota in self._session.scalars(
                select(RuntimeSpaceQuota)
                .where(
                    RuntimeSpaceQuota.workspace_id == runtime_space.workspace_id,
                    RuntimeSpaceQuota.runtime_space_id == runtime_space.id,
                    RuntimeSpaceQuota.quota_key.in_(quota_keys),
                )
                .with_for_update()
            ).all()
        }
        release_time = datetime.now(UTC)
        released_keys: list[str] = []
        for reservation in reservations:
            released_keys.append(reservation.reservation_key)
            for quota_key, amount in reservation_usage(reservation).items():
                quota = quotas.get(quota_key)
                if quota is not None:
                    quota.reserved_value = max(0, quota.reserved_value - amount)
            reservation.status = "released"
            reservation.released_at = release_time
        self._session.flush([*reservations, *quotas.values()])
        return RuntimeSpaceForceReleaseResult(len(reservations), released_keys)

    def _try_increment_quota(self, quota: RuntimeSpaceQuota, amount: int) -> bool:
        result = self._session.execute(
            update(RuntimeSpaceQuota)
            .where(
                RuntimeSpaceQuota.id == quota.id,
                RuntimeSpaceQuota.reserved_value + amount <= RuntimeSpaceQuota.limit_value,
            )
            .values(reserved_value=RuntimeSpaceQuota.reserved_value + amount)
        )
        if result.rowcount != 1:
            return False
        self._session.expire(quota, ["reserved_value"])
        return True

    def _rollback_quota_increments(
        self,
        increments: list[tuple[RuntimeSpaceQuota, int]],
    ) -> None:
        for quota, amount in reversed(increments):
            self._session.execute(
                update(RuntimeSpaceQuota)
                .where(RuntimeSpaceQuota.id == quota.id)
                .values(reserved_value=RuntimeSpaceQuota.reserved_value - amount)
            )
            self._session.expire(quota, ["reserved_value"])

    def _append_event(
        self,
        runtime_space: RuntimeSpace,
        event_type: str,
        message: str,
        metadata: dict[str, object] | None = None,
    ) -> RuntimeSpaceEvent:
        event = RuntimeSpaceEvent(
            workspace_id=runtime_space.workspace_id,
            runtime_space_id=runtime_space.id,
            event_type=event_type,
            message=message,
            event_metadata=metadata or {},
            created_at=datetime.now(UTC),
        )
        self._session.add(event)
        return event


def normalize_reservation_usage(resource_usage: dict[str, int] | None) -> dict[str, int]:
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
        reservation.resource_usage if isinstance(reservation.resource_usage, dict) else None
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
    task_id: UUID | None,
    task_step_id: UUID | None,
    resource_usage: dict[str, int],
) -> bool:
    return (
        reservation.task_id == task_id
        and reservation.task_step_id == task_step_id
        and reservation_usage(reservation) == resource_usage
    )
