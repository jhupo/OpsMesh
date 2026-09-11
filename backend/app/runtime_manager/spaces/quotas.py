from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.runtime_manager.spaces.models import RuntimeSpace, RuntimeSpaceQuota


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
