"""Limits for persistent schedule definitions and in-process execution."""

from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Settings(BaseModel):
    """Configure one PostgreSQL-locked scheduler namespace."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    namespace: str = Field(default="default", min_length=1, max_length=100)
    timezone: str = "UTC"
    max_schedules: int = Field(default=1000, ge=1, le=100000)
    max_concurrency: int = Field(default=8, ge=1, le=1000)
    reconcile_seconds: float = Field(default=5, ge=0.1, le=3600)
    timeout_seconds: float = Field(default=300, gt=0, le=86400)
    misfire_grace_seconds: int = Field(default=30, ge=1, le=86400)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        """Reject unknown IANA timezone names before startup."""
        ZoneInfo(value)
        return value
