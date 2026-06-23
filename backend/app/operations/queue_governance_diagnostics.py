from __future__ import annotations

from uuid import UUID

from redis import Redis
from sqlalchemy.orm import Session

from backend.app.api.schemas.operation_queue import QueueGovernanceDiagnosticsResponse
from backend.app.operations.queue_governance_response_builder import queue_governance_response
from backend.app.operations.queue_governance_snapshot_builder import (
    QueueGovernanceSnapshotBuilder,
)
from backend.app.redis.keys import RedisKeyBuilder


class QueueGovernanceDiagnosticsService:
    def __init__(
        self,
        session: Session,
        redis: Redis[str],
        key_builder: RedisKeyBuilder,
    ) -> None:
        self._snapshot_builder = QueueGovernanceSnapshotBuilder(session, redis, key_builder)

    def queue_governance(
        self,
        *,
        workspace_id: UUID,
        queue_name: str,
        scan_limit: int = 500,
        stale_after_seconds: int = 900,
    ) -> QueueGovernanceDiagnosticsResponse:
        snapshot = self._snapshot_builder.build(
            workspace_id=workspace_id,
            queue_name=queue_name,
            scan_limit=scan_limit,
            stale_after_seconds=stale_after_seconds,
        )
        return queue_governance_response(snapshot)
