"""Database plugin settings owned by the plugin, not the host."""

from os import environ

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .errors import DatabaseUnavailableError

_ASYNC_DSN_PREFIX = "postgresql+asyncpg://"


class Settings(BaseModel):
    """PostgreSQL connection and migration settings."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dsn: str | None = Field(default=None, repr=False)
    pool_size: int = Field(default=5, ge=1)
    max_overflow: int = Field(default=10, ge=0)
    pool_timeout: float = Field(default=30.0, gt=0)
    pool_recycle: int = Field(default=1800, gt=0)
    echo: bool = False
    migration_lock_timeout_ms: int = Field(default=60_000, gt=0)

    @field_validator("dsn")
    @classmethod
    def _require_async_postgresql(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith(_ASYNC_DSN_PREFIX):
            raise ValueError(
                f"Database DSN must start with {_ASYNC_DSN_PREFIX!r}"
            )
        return value

    def connection_dsn(self) -> str:
        """Resolve the deployment credential without logging its value."""
        dsn = environ.get("OPSMESH_DATABASE_DSN", self.dsn)
        if not dsn or not dsn.startswith(_ASYNC_DSN_PREFIX):
            raise DatabaseUnavailableError(
                "Configure OPSMESH_DATABASE_DSN with a PostgreSQL async DSN"
            )
        return dsn
