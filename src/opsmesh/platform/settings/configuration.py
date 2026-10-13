"""Database-owned operational settings. Defaults are used only when provisioning a row."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.governance.policies.events import append_policy_event
from opsmesh.governance.policies.models import PlatformPolicy

CONFIGURATION_KEY = "operational_configuration"


class ConfigurationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FileIOLimits(ConfigurationModel):
    input_files: int = Field(default=512, ge=1, le=100000)
    output_files: int = Field(default=128, ge=1, le=100000)
    input_bytes: int = Field(default=536870912, ge=1)
    output_bytes: int = Field(default=1073741824, ge=1)
    staged_file_bytes: int = Field(default=536870912, ge=1)
    agent_read_bytes: int = Field(default=1048576, ge=1)
    readable_content_types: list[str] = Field(
        default_factory=lambda: [
            "application/json",
            "application/toml",
            "application/x-yaml",
            "application/xml",
            "text/csv",
            "text/markdown",
            "text/plain",
            "text/tab-separated-values",
            "text/x-python",
            "text/xml",
            "text/yaml",
        ]
    )
    knowledge_source_bytes: int = Field(default=10485760, ge=1)


class OperationalConfiguration(ConfigurationModel):
    provider_failure_threshold: int = Field(default=3, ge=1, le=1000)
    provider_health_schedule_limit: int = Field(default=10, ge=1, le=10000)
    smtp_timeout_seconds: float = Field(default=5, gt=0, le=300)
    memory_chunk_size: int = Field(default=900, ge=1, le=100000)
    memory_chunk_overlap: int = Field(default=120, ge=0)
    memory_source_limit: int = Field(default=80, ge=1, le=100000)
    memory_snippet_length: int = Field(default=220, ge=1, le=10000)
    default_embedding_model: str = Field(default="text-embedding-3-small", min_length=1)
    default_embedding_dimensions: int = Field(default=1536, ge=1, le=16000)
    manager_task_capacity: int = Field(default=1, ge=1, le=10000)
    files: FileIOLimits = Field(default_factory=FileIOLimits)

    @model_validator(mode="after")
    def validate_relationships(self) -> OperationalConfiguration:
        if self.memory_chunk_overlap >= self.memory_chunk_size:
            raise ValueError("Memory chunk overlap must be smaller than chunk size")
        return self


def operational_configuration(session: Session) -> OperationalConfiguration:
    row = session.scalar(
        select(PlatformPolicy).where(
            PlatformPolicy.policy_key == CONFIGURATION_KEY,
            PlatformPolicy.status == "active",
        )
    )
    if row is None:
        raise ValueError("Operational configuration is missing; apply database migrations")
    return OperationalConfiguration.model_validate(row.value)


class OperationalConfigurationService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self) -> OperationalConfiguration:
        return operational_configuration(self.session)

    def replace(
        self, value: OperationalConfiguration, *, actor_id: str
    ) -> OperationalConfiguration:
        row = self.session.scalar(
            select(PlatformPolicy)
            .where(
                PlatformPolicy.policy_key == CONFIGURATION_KEY,
            )
            .with_for_update()
        )
        if row is None:
            raise ValueError("Operational configuration is missing; apply database migrations")
        row.value = value.model_dump(mode="json")
        row.updated_by = actor_id
        append_policy_event(
            self.session,
            row,
            "configuration.updated",
            "Operational configuration updated",
            {"value": row.value, "actor_id": actor_id},
        )
        self.session.commit()
        return value
