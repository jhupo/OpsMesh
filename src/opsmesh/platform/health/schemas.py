"""Stable platform health response contracts."""

from pydantic import BaseModel, ConfigDict


class HealthView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str


class VersionView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: str
    version: str
    environment: str
    build: str | None
