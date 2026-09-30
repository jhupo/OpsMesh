from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.app.workspaces.projects.models import WorkspaceProject, WorkspaceProjectQuota
from backend.app.workspaces.quotas.models import WorkspaceQuota, WorkspaceReservation

ACTIVE_RUNS_QUOTA_KEY = "active_runs"


@dataclass(frozen=True)
class WorkspaceReservationResult:
    reservation: WorkspaceReservation | None
    blocked_reason: str | None = None


class WorkspaceQuotaService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def reserve(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID | None,
        task_step_id: UUID | None,
        project_id: UUID | None = None,
        reservation_key: str,
        resource_usage: dict[str, int],
        ensure_active_run: bool = True,
    ) -> WorkspaceReservationResult:
        usage = _normalize_usage(resource_usage, ensure_active_run=ensure_active_run)
        reservation = self._session.scalar(
            select(WorkspaceReservation)
            .where(
                WorkspaceReservation.workspace_id == workspace_id,
                WorkspaceReservation.reservation_key == reservation_key,
            )
            .with_for_update()
        )
        if reservation is not None and reservation.status == "active":
            if not _active_reservation_matches(
                reservation=reservation,
                task_id=task_id,
                task_step_id=task_step_id,
                project_id=project_id,
                resource_usage=usage,
            ):
                return WorkspaceReservationResult(
                    reservation=None,
                    blocked_reason="workspace_reservation_conflict",
                )
            return WorkspaceReservationResult(reservation=reservation)

        project_quotas: dict[str, WorkspaceProjectQuota] = {}
        if project_id is not None:
            project = self._session.scalar(
                select(WorkspaceProject).where(
                    WorkspaceProject.workspace_id == workspace_id,
                    WorkspaceProject.id == project_id,
                    WorkspaceProject.status == "active",
                )
            )
            if project is None:
                return WorkspaceReservationResult(
                    reservation=None,
                    blocked_reason="workspace_project_not_found",
                )
            project_quotas = {
                quota.quota_key: quota
                for quota in self._session.scalars(
                    select(WorkspaceProjectQuota)
                    .where(
                        WorkspaceProjectQuota.workspace_id == workspace_id,
                        WorkspaceProjectQuota.project_id == project_id,
                        WorkspaceProjectQuota.status == "active",
                        WorkspaceProjectQuota.quota_key.in_(usage),
                    )
                    .with_for_update()
                ).all()
            }
        quotas = {
            quota.quota_key: quota
            for quota in self._session.scalars(
                select(WorkspaceQuota)
                .where(
                    WorkspaceQuota.workspace_id == workspace_id,
                    WorkspaceQuota.status == "active",
                    WorkspaceQuota.quota_key.in_(usage),
                )
                .with_for_update()
            ).all()
        }
        exceeded = _first_exceeded_quota(quotas, usage)
        if exceeded is not None:
            return WorkspaceReservationResult(
                reservation=None,
                blocked_reason=f"workspace_quota_exceeded:{exceeded.quota_key}",
            )
        exceeded_project = _first_exceeded_quota(project_quotas, usage)
        if exceeded_project is not None:
            return WorkspaceReservationResult(
                reservation=None,
                blocked_reason=f"workspace_project_quota_exceeded:{exceeded_project.quota_key}",
            )

        incremented: list[tuple[WorkspaceQuota | WorkspaceProjectQuota, int]] = []
        for quota_key, amount in usage.items():
            quota = quotas.get(quota_key)
            if quota is None:
                continue
            if not self._try_increment_quota(quota, amount):
                self._rollback_quota_increments(incremented)
                return WorkspaceReservationResult(
                    reservation=None,
                    blocked_reason=f"workspace_quota_exceeded:{quota.quota_key}",
                )
            incremented.append((quota, amount))
        for quota_key, amount in usage.items():
            project_quota = project_quotas.get(quota_key)
            if project_quota is None:
                continue
            if not self._try_increment_quota(project_quota, amount):
                self._rollback_quota_increments(incremented)
                return WorkspaceReservationResult(
                    reservation=None,
                    blocked_reason=f"workspace_project_quota_exceeded:{project_quota.quota_key}",
                )
            incremented.append((project_quota, amount))

        if reservation is None:
            reservation = WorkspaceReservation(
                workspace_id=workspace_id,
                workspace_project_id=project_id,
                task_id=task_id,
                task_step_id=task_step_id,
                reservation_key=reservation_key,
                resource_usage=dict(usage),
            )
            self._session.add(reservation)
        else:
            reservation.task_id = task_id
            reservation.task_step_id = task_step_id
            reservation.workspace_project_id = project_id
            reservation.agent_run_id = None
            reservation.resource_usage = dict(usage)
            reservation.status = "active"
            reservation.released_at = None
            reservation.expires_at = None
        self._session.flush([reservation, *quotas.values()])
        return WorkspaceReservationResult(reservation=reservation)

    def _try_increment_quota(
        self,
        quota: WorkspaceQuota | WorkspaceProjectQuota,
        amount: int,
    ) -> bool:
        quota_model = (
            WorkspaceProjectQuota if isinstance(quota, WorkspaceProjectQuota) else WorkspaceQuota
        )
        reserved_id = self._session.scalar(
            update(quota_model)
            .where(
                quota_model.id == quota.id,
                quota_model.workspace_id == quota.workspace_id,
                quota_model.reserved_value + amount <= quota_model.limit_value,
            )
            .values(reserved_value=quota_model.reserved_value + amount)
            .returning(quota_model.id)
        )
        if reserved_id is None:
            return False
        self._session.expire(quota, ["reserved_value"])
        return True

    def _rollback_quota_increments(
        self,
        increments: list[tuple[WorkspaceQuota | WorkspaceProjectQuota, int]],
    ) -> None:
        for quota, amount in reversed(increments):
            quota_model = (
                WorkspaceProjectQuota
                if isinstance(quota, WorkspaceProjectQuota)
                else WorkspaceQuota
            )
            self._session.execute(
                update(quota_model)
                .where(
                    quota_model.id == quota.id,
                    quota_model.workspace_id == quota.workspace_id,
                )
                .values(reserved_value=quota_model.reserved_value - amount)
            )
            self._session.expire(quota, ["reserved_value"])

    def attach_reservation_to_run(
        self,
        reservation: WorkspaceReservation,
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
            select(WorkspaceReservation).where(
                WorkspaceReservation.workspace_id == workspace_id,
                WorkspaceReservation.agent_run_id == agent_run_id,
                WorkspaceReservation.status == "active",
            )
        ).all()
        for reservation in reservations:
            for quota_key, amount in _reservation_usage(reservation).items():
                usage[quota_key] = usage.get(quota_key, 0) + amount
        return usage

    def release_reservation(
        self,
        reservation: WorkspaceReservation,
        *,
        released_at: datetime | None = None,
    ) -> None:
        locked = self._session.scalar(
            select(WorkspaceReservation)
            .where(
                WorkspaceReservation.workspace_id == reservation.workspace_id,
                WorkspaceReservation.id == reservation.id,
            )
            .with_for_update()
        )
        if locked is None or locked.status != "active":
            return
        reservation = locked
        quotas = {
            quota.quota_key: quota
            for quota in self._session.scalars(
                select(WorkspaceQuota)
                .where(
                    WorkspaceQuota.workspace_id == reservation.workspace_id,
                    WorkspaceQuota.quota_key.in_(_reservation_usage(reservation)),
                )
                .with_for_update()
            ).all()
        }
        for quota_key, amount in _reservation_usage(reservation).items():
            quota = quotas.get(quota_key)
            if quota is not None:
                quota.reserved_value = max(0, quota.reserved_value - amount)
        project_quotas = (
            {
                quota.quota_key: quota
                for quota in self._session.scalars(
                    select(WorkspaceProjectQuota)
                    .where(
                        WorkspaceProjectQuota.workspace_id == reservation.workspace_id,
                        WorkspaceProjectQuota.project_id == reservation.workspace_project_id,
                        WorkspaceProjectQuota.quota_key.in_(_reservation_usage(reservation)),
                    )
                    .with_for_update()
                ).all()
            }
            if reservation.workspace_project_id is not None
            else {}
        )
        for quota_key, amount in _reservation_usage(reservation).items():
            project_quota = project_quotas.get(quota_key)
            if project_quota is not None:
                project_quota.reserved_value = max(0, project_quota.reserved_value - amount)
        reservation.status = "released"
        reservation.released_at = released_at or datetime.now(UTC)
        self._session.flush([reservation, *quotas.values(), *project_quotas.values()])

    def release_reservations_for_run(
        self,
        *,
        workspace_id: UUID,
        agent_run_id: UUID,
        released_at: datetime | None = None,
    ) -> int:
        release_time = released_at or datetime.now(UTC)
        reservations = self._session.scalars(
            select(WorkspaceReservation)
            .where(
                WorkspaceReservation.workspace_id == workspace_id,
                WorkspaceReservation.agent_run_id == agent_run_id,
                WorkspaceReservation.status == "active",
            )
            .with_for_update()
        ).all()
        for reservation in reservations:
            self.release_reservation(reservation, released_at=release_time)
        return len(reservations)


def _normalize_usage(
    resource_usage: dict[str, int],
    *,
    ensure_active_run: bool,
) -> dict[str, int]:
    usage = {key: value for key, value in resource_usage.items() if value > 0}
    if ensure_active_run:
        usage[ACTIVE_RUNS_QUOTA_KEY] = max(1, usage.get(ACTIVE_RUNS_QUOTA_KEY, 1))
    return usage


def _reservation_usage(reservation: WorkspaceReservation) -> dict[str, int]:
    usage: dict[str, int] = {}
    for quota_key, value in reservation.resource_usage.items():
        if isinstance(value, int) and value > 0:
            usage[quota_key] = value
    return usage


def _first_exceeded_quota(
    quotas: Mapping[str, WorkspaceQuota | WorkspaceProjectQuota],
    usage: dict[str, int],
) -> WorkspaceQuota | WorkspaceProjectQuota | None:
    for quota_key, amount in usage.items():
        quota = quotas.get(quota_key)
        if quota is not None and quota.reserved_value + amount > quota.limit_value:
            return quota
    return None


def _active_reservation_matches(
    *,
    reservation: WorkspaceReservation,
    task_id: UUID | None,
    task_step_id: UUID | None,
    project_id: UUID | None,
    resource_usage: dict[str, int],
) -> bool:
    if reservation.task_id != task_id:
        return False
    if reservation.task_step_id != task_step_id:
        return False
    if reservation.workspace_project_id != project_id:
        return False
    existing_usage = {
        key: value
        for key, value in reservation.resource_usage.items()
        if isinstance(value, int) and value > 0
    }
    return existing_usage == resource_usage
