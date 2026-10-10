
from pydantic import (
    BaseModel,
)


class AdminSystemConfigurationResponse(BaseModel):
    settings: dict[str, object]
    recommended_resources: dict[str, int]
    configured_resources: dict[str, int]
    resource_deltas: dict[str, int]
    blocking_executor: dict[str, int | bool]
    database_pool: dict[str, int | str | None]
    redis_pool: dict[str, int | None]
