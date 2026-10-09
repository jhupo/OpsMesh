from dataclasses import asdict

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.identity.authorization.admin_actor import require_admin_actor
from backend.app.identity.authorization.admin_dependencies import require_platform_admin
from backend.app.identity.authorization.context import AuthenticatedUser
from backend.app.platform.settings.policy import (
    OperationalConfiguration,
    OperationalConfigurationService,
)
from backend.app.platform.settings.schemas import AdminSystemConfigurationResponse
from backend.app.shared.config import Settings, get_settings
from backend.app.shared.db.session import database_pool_snapshot, get_db_session
from backend.app.shared.executors import blocking_executor_snapshot
from backend.app.shared.feature_flags import DEFAULT_FEATURE_FLAGS, get_feature_flags
from backend.app.shared.redis.client import redis_pool_snapshot
from backend.app.shared.redis.dependencies import RedisClient, get_redis_client
from backend.app.shared.resources import recommend_runtime_resources

router = APIRouter(dependencies=[Depends(require_platform_admin)])


@router.get("/system/configuration", response_model=AdminSystemConfigurationResponse)
async def admin_system_configuration(
    settings: Settings = Depends(get_settings),
    redis: RedisClient = Depends(get_redis_client),
) -> AdminSystemConfigurationResponse:
    recommendation = recommend_runtime_resources()
    recommended_resources = recommendation.as_dict()
    configured_resources = {
        "database_pool_size": settings.database_pool_size,
        "database_max_overflow": settings.database_max_overflow,
        "blocking_thread_pool_workers": settings.blocking_thread_pool_workers,
        "redis_max_connections": settings.redis_max_connections,
    }
    resource_deltas = {
        key: configured_resources[key] - recommended_resources[key] for key in configured_resources
    }
    feature_flags = get_feature_flags(settings=settings)
    settings_summary = settings.redacted_summary()
    settings_summary["resolved_feature_flags"] = {
        key: asdict(feature_flags.decision(key))
        for key in sorted(set(DEFAULT_FEATURE_FLAGS) | set(settings.feature_flags))
    }
    return AdminSystemConfigurationResponse(
        settings=settings_summary,
        recommended_resources=recommended_resources,
        configured_resources=configured_resources,
        resource_deltas=resource_deltas,
        blocking_executor=blocking_executor_snapshot(settings).as_dict(),
        database_pool=database_pool_snapshot().as_dict(),
        redis_pool=redis_pool_snapshot(redis).as_dict(),
    )


@router.get("/system/operational-configuration", response_model=OperationalConfiguration)
def get_operational_configuration(
    session: Session = Depends(get_db_session),
) -> OperationalConfiguration:
    return OperationalConfigurationService(session).get()


@router.put("/system/operational-configuration", response_model=OperationalConfiguration)
def replace_operational_configuration(
    value: OperationalConfiguration,
    actor: AuthenticatedUser = Depends(require_admin_actor),
    session: Session = Depends(get_db_session),
) -> OperationalConfiguration:
    return OperationalConfigurationService(session).replace(value, actor_id=str(actor.user_id))
