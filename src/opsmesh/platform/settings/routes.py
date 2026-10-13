from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from opsmesh.identity.authorization.admin_actor import require_admin_actor
from opsmesh.identity.authorization.admin_dependencies import require_platform_admin
from opsmesh.identity.authorization.context import AuthenticatedUser
from opsmesh.platform.settings.configuration import (
    OperationalConfiguration,
    OperationalConfigurationService,
)
from opsmesh.platform.settings.schemas import AdminSystemConfigurationResponse
from opsmesh.shared.config import Settings, get_settings
from opsmesh.shared.db.session import database_pool_snapshot, get_db_session
from opsmesh.shared.pool_sizing import recommend_connection_pools
from opsmesh.shared.redis.client import redis_pool_snapshot
from opsmesh.shared.redis.dependencies import RedisClient, get_redis_client

router = APIRouter(dependencies=[Depends(require_platform_admin)])


@router.get("/system/configuration", response_model=AdminSystemConfigurationResponse)
def admin_system_configuration(
    settings: Settings = Depends(get_settings),
    redis: RedisClient = Depends(get_redis_client),
) -> AdminSystemConfigurationResponse:
    recommendation = recommend_connection_pools()
    recommended_resources = recommendation.as_dict()
    configured_resources = {
        "database_pool_size": settings.database_pool_size,
        "database_max_overflow": settings.database_max_overflow,
        "redis_max_connections": settings.redis_max_connections,
    }
    resource_deltas = {
        key: configured_resources[key] - recommended_resources[key] for key in configured_resources
    }
    settings_summary = settings.redacted_summary()
    return AdminSystemConfigurationResponse(
        settings=settings_summary,
        recommended_resources=recommended_resources,
        configured_resources=configured_resources,
        resource_deltas=resource_deltas,
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
