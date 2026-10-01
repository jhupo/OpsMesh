from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Depends
from sqlalchemy.orm import Session

from backend.app.runtime.queues.admin_service import AdminQueueOperationsService
from backend.app.shared.config import Settings, get_settings
from backend.app.shared.db.session import get_db_session
from backend.app.shared.redis.dependencies import get_redis_client
from backend.app.shared.redis.keys import RedisKeyBuilder

if TYPE_CHECKING:
    from redis import Redis

    RedisClient = Redis[str]
else:
    RedisClient = object





def admin_queue_operations_service(
    session: Session = Depends(get_db_session),
    redis: RedisClient = Depends(get_redis_client),
    settings: Settings = Depends(get_settings),
) -> AdminQueueOperationsService:
    return AdminQueueOperationsService(
        session,
        redis,
        RedisKeyBuilder(settings.redis_key_prefix),
    )
