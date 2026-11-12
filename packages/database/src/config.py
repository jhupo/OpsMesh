"""Database plugin settings owned by the plugin, not the host."""

from pydantic import BaseModel, ConfigDict, Field, field_validator

CONFIG_NAMESPACE = "opsmesh.plugins.database"

_ASYNC_DSN_PREFIX = "postgresql+asyncpg://"


class Settings(BaseModel):
    """PostgreSQL connection and migration settings."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dsn: str
    pool_size: int = Field(default=5, ge=1)
    max_overflow: int = Field(default=10, ge=0)
    pool_timeout: float = Field(default=30.0, gt=0)
    pool_recycle: int = Field(default=1800, gt=0)
    echo: bool = False
    migration_lock_timeout_ms: int = Field(default=60_000, gt=0)

    @field_validator("dsn")
    @classmethod
    def _require_async_postgresql(cls, value: str) -> str:
        if not value.startswith(_ASYNC_DSN_PREFIX):
            raise ValueError(
                f"Database DSN must start with {_ASYNC_DSN_PREFIX!r}"
            )
        return value
