from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Depends
from sqlalchemy.orm import Session

from backend.app.admin.operations_summary import AdminOperationsSummaryService
from backend.app.admin.overview import AdminOverviewService
from backend.app.admin.policy_control import AdminPolicyService
from backend.app.admin.queue_operations import AdminQueueOperationsService
from backend.app.admin.runtime_control import AdminRuntimeService
from backend.app.admin.security_events import AdminSecurityEventService
from backend.app.admin.worker_policy_control import AdminWorkerPolicyControlService
from backend.app.admin.workers import AdminWorkerService
from backend.app.core.config import Settings, get_settings
from backend.app.db.session import get_db_session
from backend.app.redis.dependencies import get_redis_client
from backend.app.redis.keys import RedisKeyBuilder

if TYPE_CHECKING:
    from redis import Redis

    RedisClient = Redis[str]
else:
    RedisClient = object


def admin_overview_service(
    session: Session = Depends(get_db_session),
) -> AdminOverviewService:
    return AdminOverviewService(session)


def admin_policy_service(
    session: Session = Depends(get_db_session),
) -> AdminPolicyService:
    return AdminPolicyService(session)


def admin_worker_service(
    session: Session = Depends(get_db_session),
) -> AdminWorkerService:
    return AdminWorkerService(session, AdminWorkerPolicyControlService(session))


def admin_runtime_service(
    session: Session = Depends(get_db_session),
) -> AdminRuntimeService:
    return AdminRuntimeService(session)


def admin_security_event_service(
    session: Session = Depends(get_db_session),
) -> AdminSecurityEventService:
    return AdminSecurityEventService(session)


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
