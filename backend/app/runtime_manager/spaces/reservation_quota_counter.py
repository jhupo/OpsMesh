from sqlalchemy import update
from sqlalchemy.orm import Session

from backend.app.runtime_manager.spaces.models import RuntimeSpaceQuota


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
