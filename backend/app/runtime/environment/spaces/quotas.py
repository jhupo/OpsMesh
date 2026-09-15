from __future__ import annotations

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.app.runtime.environment.spaces.models import RuntimeSpace, RuntimeSpaceQuota


class RuntimeSpaceQuotaService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def replace_quotas(
        self,
        *,
        workspace_id: UUID,
        runtime_space: RuntimeSpace,
        quota_limits: dict[str, int],
    ) -> None:
        existing = self._session.scalars(
            select(RuntimeSpaceQuota).where(
                RuntimeSpaceQuota.workspace_id == workspace_id,
                RuntimeSpaceQuota.runtime_space_id == runtime_space.id,
            )
        ).all()
        existing_by_key = {quota.quota_key: quota for quota in existing}
        for quota_key, limit_value in quota_limits.items():
            quota = existing_by_key.pop(quota_key, None)
            if quota is None:
                quota = RuntimeSpaceQuota(
                    workspace_id=workspace_id,
                    runtime_space_id=runtime_space.id,
                    quota_key=quota_key,
                    limit_value=limit_value,
                )
                self._session.add(quota)
            else:
                quota.limit_value = limit_value
                quota.status = "active"
        for quota in existing_by_key.values():
            quota.status = "disabled"


class RuntimeSpaceQuotaCounter:
    def __init__(self, session: Session) -> None:
        self._session = session

    def try_increment(self, quota: RuntimeSpaceQuota, amount: int) -> bool:
        reserved_id = self._session.scalar(
            update(RuntimeSpaceQuota)
            .where(
                RuntimeSpaceQuota.id == quota.id,
                RuntimeSpaceQuota.workspace_id == quota.workspace_id,
                RuntimeSpaceQuota.reserved_value + amount <= RuntimeSpaceQuota.limit_value,
            )
            .values(reserved_value=RuntimeSpaceQuota.reserved_value + amount)
            .returning(RuntimeSpaceQuota.id)
        )
        if reserved_id is None:
            return False
        self._session.expire(quota, ["reserved_value"])
        return True

    def rollback_increments(self, increments: list[tuple[RuntimeSpaceQuota, int]]) -> None:
        for quota, amount in reversed(increments):
            self._session.execute(
                update(RuntimeSpaceQuota)
                .where(RuntimeSpaceQuota.id == quota.id)
                .values(reserved_value=RuntimeSpaceQuota.reserved_value - amount)
            )
            self._session.expire(quota, ["reserved_value"])

    def decrement(self, quota: RuntimeSpaceQuota, amount: int) -> None:
        quota.reserved_value = max(0, quota.reserved_value - amount)
