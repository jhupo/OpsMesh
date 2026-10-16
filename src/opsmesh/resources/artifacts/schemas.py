"""Artifact metadata and access HTTP contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

ArtifactSourceName = Literal["run", "invocation"]


class ArtifactView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: UUID
    source_kind: str
    source_id: UUID
    owner_user_id: UUID
    name: str
    digest: str
    size: int
    media_type: str | None
    labels: dict[str, object]
    created_at: datetime
