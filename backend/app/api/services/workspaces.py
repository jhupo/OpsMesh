from collections import Counter
from datetime import UTC, datetime
from typing import TypeVar
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams
from backend.app.api.schemas.workspaces import (
    WorkspaceCreateRequest,
    WorkspaceQuotaUpsertRequest,
    WorkspaceUpdateRequest,
)
from backend.app.audit.service import AuditService
from backend.app.auth.permissions import WorkspaceRole
from backend.app.db.errors import commit_or_raise_conflict
from backend.app.workspaces.models import (
    Workspace,
    WorkspaceMember,
    WorkspaceQuota,
    WorkspaceReservation,
)

EXECUTION_SLOT_QUOTA_KEYS = ("active_runs", "docker_runtimes", "self_hosted_jobs")

T = TypeVar("T")


class WorkspaceService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_for_user(self, user_id: UUID, page: PageParams) -> tuple[list[Workspace], int]:
        statement = (
            select(Workspace)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
            .where(WorkspaceMember.user_id == user_id, WorkspaceMember.status == "active")
            .order_by(Workspace.created_at.desc())
        )
        return self._page(statement, page)

    def create_for_owner(self, owner_user_id: UUID, data: WorkspaceCreateRequest) -> Workspace:
        workspace = Workspace(
            owner_user_id=owner_user_id,
            name=data.name,
            slug=data.slug,
            settings=data.settings,
        )
        membership = WorkspaceMember(
            workspace=workspace,
            user_id=owner_user_id,
            role=WorkspaceRole.OWNER.value,
        )
        self._session.add_all([workspace, membership])
        commit_or_raise_conflict(self._session, "Workspace slug already exists")
        self._session.refresh(workspace)
        return workspace

    def get_scoped(self, workspace_id: UUID) -> Workspace | None:
        return self._session.get(Workspace, workspace_id)

    def get_owned(self, owner_user_id: UUID, workspace_id: UUID) -> Workspace | None:
        return self._session.scalar(
            select(Workspace).where(
                Workspace.owner_user_id == owner_user_id,
                Workspace.id == workspace_id,
            )
        )

    def update(
        self,
        workspace: Workspace,
        data: WorkspaceUpdateRequest,
        *,
        actor_user_id: UUID | None = None,
    ) -> Workspace:
        old_status = workspace.status
        old_scheduler = _scheduler_settings(workspace.settings)
        updates = data.model_dump(exclude_unset=True)
        for field, value in updates.items():
            setattr(workspace, field, value)
        new_scheduler = _scheduler_settings(workspace.settings)
        if actor_user_id is not None:
            if workspace.status != old_status:
                AuditService(self._session).record_user_action(
                    workspace_id=workspace.id,
                    user_id=actor_user_id,
                    action="workspace.status_updated",
                    target_type="workspace",
                    target_id=workspace.id,
                    metadata={"before": old_status, "after": workspace.status},
                )
            if new_scheduler != old_scheduler:
                AuditService(self._session).record_user_action(
                    workspace_id=workspace.id,
                    user_id=actor_user_id,
                    action="workspace.scheduler_policy_updated",
                    target_type="workspace",
                    target_id=workspace.id,
                    metadata={"before": old_scheduler, "after": new_scheduler},
                )
        self._session.commit()
        self._session.refresh(workspace)
        return workspace

    def list_members(
        self,
        workspace_id: UUID,
        page: PageParams,
    ) -> tuple[list[WorkspaceMember], int]:
        statement = (
            select(WorkspaceMember)
            .where(WorkspaceMember.workspace_id == workspace_id)
            .order_by(WorkspaceMember.created_at.desc())
        )
        return self._page(statement, page)

    def list_quotas(self, workspace_id: UUID, page: PageParams) -> tuple[list[WorkspaceQuota], int]:
        statement = (
            select(WorkspaceQuota)
            .where(WorkspaceQuota.workspace_id == workspace_id)
            .order_by(WorkspaceQuota.status, WorkspaceQuota.quota_key)
        )
        return self._page(statement, page)

    def upsert_quotas(
        self,
        workspace_id: UUID,
        data: WorkspaceQuotaUpsertRequest,
        *,
        actor_user_id: UUID | None = None,
    ) -> list[WorkspaceQuota]:
        existing = {
            quota.quota_key: quota
            for quota in self._session.scalars(
                select(WorkspaceQuota)
                .where(WorkspaceQuota.workspace_id == workspace_id)
                .with_for_update()
            ).all()
        }
        updated: list[WorkspaceQuota] = []
        audit_items: list[dict[str, object]] = []
        for item in data.quotas:
            quota = existing.get(item.quota_key)
            if quota is None:
                quota = WorkspaceQuota(
                    workspace_id=workspace_id,
                    quota_key=item.quota_key,
                    limit_value=item.limit_value,
                    unit=item.unit,
                )
                self._session.add(quota)
                before: dict[str, object] | None = None
            else:
                before = _quota_snapshot(quota)
                quota.limit_value = item.limit_value
                quota.unit = item.unit
                quota.status = "active"
            updated.append(quota)
            audit_items.append(
                {
                    "quota_key": item.quota_key,
                    "before": before,
                    "after": {
                        "quota_key": item.quota_key,
                        "limit_value": item.limit_value,
                        "unit": item.unit,
                        "status": "active",
                    },
                }
            )
        if actor_user_id is not None:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="workspace.quotas_upserted",
                target_type="workspace",
                target_id=workspace_id,
                metadata={"quotas": audit_items},
            )
        self._session.commit()
        for quota in updated:
            self._session.refresh(quota)
        return sorted(updated, key=lambda quota: quota.quota_key)

    def disable_quota(
        self,
        workspace_id: UUID,
        quota_key: str,
        *,
        actor_user_id: UUID | None = None,
    ) -> WorkspaceQuota | None:
        quota = self._session.scalar(
            select(WorkspaceQuota)
            .where(
                WorkspaceQuota.workspace_id == workspace_id,
                WorkspaceQuota.quota_key == quota_key,
            )
            .with_for_update()
        )
        if quota is None:
            return None
        before = _quota_snapshot(quota)
        quota.status = "disabled"
        if actor_user_id is not None:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="workspace.quota_disabled",
                target_type="workspace_quota",
                target_id=quota.id,
                metadata={
                    "quota_key": quota.quota_key,
                    "before": before,
                    "after": _quota_snapshot(quota),
                    "over_reserved": quota.reserved_value > quota.limit_value,
                },
            )
        self._session.commit()
        self._session.refresh(quota)
        return quota

    def execution_slot_summary(self, workspace_id: UUID) -> dict[str, object]:
        quotas = list(
            self._session.scalars(
                select(WorkspaceQuota)
                .where(
                    WorkspaceQuota.workspace_id == workspace_id,
                    WorkspaceQuota.quota_key.in_(EXECUTION_SLOT_QUOTA_KEYS),
                )
                .order_by(WorkspaceQuota.quota_key.asc())
            )
        )
        reservations = list(
            self._session.scalars(
                select(WorkspaceReservation)
                .where(
                    WorkspaceReservation.workspace_id == workspace_id,
                    WorkspaceReservation.status == "active",
                )
                .order_by(WorkspaceReservation.created_at.asc())
            )
        )
        active_reservation_usage: Counter[str] = Counter()
        over_reserved_quota_keys: list[str] = []
        for quota in quotas:
            if quota.reserved_value > quota.limit_value:
                over_reserved_quota_keys.append(quota.quota_key)
        active_reservation_count = 0
        for reservation in reservations:
            active_reservation_count += 1
            for quota_key, amount in _reservation_usage(reservation).items():
                if quota_key in EXECUTION_SLOT_QUOTA_KEYS:
                    active_reservation_usage[quota_key] += amount
        return {
            "workspace_id": workspace_id,
            "generated_at": datetime.now(UTC),
            "quotas": quotas,
            "active_reservations": reservations,
            "active_reservation_count": active_reservation_count,
            "reservation_usage": dict(sorted(active_reservation_usage.items())),
            "over_reserved_quota_keys": sorted(over_reserved_quota_keys),
        }

    def _page(self, statement: Select[tuple[T]], page: PageParams) -> tuple[list[T], int]:
        total = self._session.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        )
        rows = self._session.scalars(statement.limit(page.limit).offset(page.offset)).all()
        return list(rows), int(total or 0)


def _scheduler_settings(settings: dict[str, object]) -> dict[str, object]:
    raw_scheduler = settings.get("scheduler") if isinstance(settings, dict) else None
    if not isinstance(raw_scheduler, dict):
        return {}
    return dict(raw_scheduler)


def _quota_snapshot(quota: WorkspaceQuota) -> dict[str, object]:
    return {
        "quota_key": quota.quota_key,
        "limit_value": quota.limit_value,
        "reserved_value": quota.reserved_value,
        "unit": quota.unit,
        "status": quota.status,
    }


def _reservation_usage(reservation: WorkspaceReservation) -> dict[str, int]:
    usage: dict[str, int] = {}
    for quota_key, value in reservation.resource_usage.items():
        if isinstance(quota_key, str) and isinstance(value, int) and value > 0:
            usage[quota_key] = value
    return usage
