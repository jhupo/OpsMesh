from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.observability.audit.service import AuditService
from backend.app.workspaces.management.snapshots import quota_snapshot
from backend.app.workspaces.projects.models import WorkspaceProject, WorkspaceProjectQuota
from backend.app.workspaces.quotas.contracts import WorkspaceQuotaUpsertPayload
from backend.app.workspaces.quotas.models import WorkspaceQuota, WorkspaceReservation

EXECUTION_SLOT_QUOTA_KEYS = ("active_runs", "docker_runtimes", "self_hosted_jobs")


class WorkspaceQuotaService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert_quotas(
        self,
        workspace_id: UUID,
        data: WorkspaceQuotaUpsertPayload,
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
                before = quota_snapshot(quota)
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
        before = quota_snapshot(quota)
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
                    "after": quota_snapshot(quota),
                    "over_reserved": quota.reserved_value > quota.limit_value,
                },
            )
        self._session.commit()
        self._session.refresh(quota)
        return quota

    def list_project_quotas(
        self,
        workspace_id: UUID,
        project_id: UUID,
    ) -> list[WorkspaceProjectQuota]:
        return list(
            self._session.scalars(
                select(WorkspaceProjectQuota)
                .where(
                    WorkspaceProjectQuota.workspace_id == workspace_id,
                    WorkspaceProjectQuota.project_id == project_id,
                )
                .order_by(WorkspaceProjectQuota.quota_key)
            ).all()
        )

    def upsert_project_quotas(
        self,
        workspace_id: UUID,
        project_id: UUID,
        data: WorkspaceQuotaUpsertPayload,
        *,
        actor_id: str = "platform_admin",
    ) -> list[WorkspaceProjectQuota] | None:
        project = self._session.scalar(
            select(WorkspaceProject)
            .where(
                WorkspaceProject.workspace_id == workspace_id,
                WorkspaceProject.id == project_id,
            )
            .with_for_update()
        )
        if project is None:
            return None
        existing = {
            quota.quota_key: quota
            for quota in self._session.scalars(
                select(WorkspaceProjectQuota)
                .where(
                    WorkspaceProjectQuota.workspace_id == workspace_id,
                    WorkspaceProjectQuota.project_id == project_id,
                )
                .with_for_update()
            ).all()
        }
        updated: list[WorkspaceProjectQuota] = []
        audit_items: list[dict[str, object]] = []
        for item in data.quotas:
            quota = existing.get(item.quota_key)
            before = quota_snapshot(quota) if quota is not None else None
            if quota is None:
                quota = WorkspaceProjectQuota(
                    workspace_id=workspace_id,
                    project_id=project_id,
                    quota_key=item.quota_key,
                    limit_value=item.limit_value,
                    unit=item.unit,
                )
                self._session.add(quota)
            else:
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
        AuditService(self._session).record_system_action(
            workspace_id=workspace_id,
            action="platform.workspace.project_quotas_upserted",
            target_type="workspace_project",
            target_id=project_id,
            metadata={"quotas": audit_items, "actor_id": actor_id},
            actor_id=actor_id,
        )
        self._session.commit()
        for quota in updated:
            self._session.refresh(quota)
        return sorted(updated, key=lambda quota: quota.quota_key)

    def disable_project_quota(
        self,
        workspace_id: UUID,
        project_id: UUID,
        quota_key: str,
        *,
        actor_id: str = "platform_admin",
    ) -> WorkspaceProjectQuota | None:
        quota = self._session.scalar(
            select(WorkspaceProjectQuota)
            .where(
                WorkspaceProjectQuota.workspace_id == workspace_id,
                WorkspaceProjectQuota.project_id == project_id,
                WorkspaceProjectQuota.quota_key == quota_key,
            )
            .with_for_update()
        )
        if quota is None:
            return None
        before = quota_snapshot(quota)
        quota.status = "disabled"
        AuditService(self._session).record_system_action(
            workspace_id=workspace_id,
            action="platform.workspace.project_quota_disabled",
            target_type="workspace_project_quota",
            target_id=quota.id,
            metadata={"before": before, "after": quota_snapshot(quota)},
            actor_id=actor_id,
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
            for quota_key, amount in reservation_usage(reservation).items():
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


def reservation_usage(reservation: WorkspaceReservation) -> dict[str, int]:
    usage: dict[str, int] = {}
    for quota_key, value in reservation.resource_usage.items():
        if isinstance(quota_key, str) and isinstance(value, int) and value > 0:
            usage[quota_key] = value
    return usage
