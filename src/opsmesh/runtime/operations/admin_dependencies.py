from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Depends
from sqlalchemy.orm import Session

from opsmesh.runtime.operations.admin_summary import AdminOperationsSummaryService
from opsmesh.shared.config import Settings, get_settings
from opsmesh.shared.db.session import get_db_session
from opsmesh.shared.redis.dependencies import get_redis_client
from opsmesh.shared.redis.keys import RedisKeyBuilder

if TYPE_CHECKING:
    from redis import Redis

    RedisClient = Redis[str]
else:
    RedisClient = object





def admin_operations_summary_service(
    session: Session = Depends(get_db_session),
    redis: RedisClient = Depends(get_redis_client),
    settings: Settings = Depends(get_settings),
) -> AdminOperationsSummaryService:
    return AdminOperationsSummaryService(
        session,
        redis,
        RedisKeyBuilder(settings.redis_key_prefix),
    )
