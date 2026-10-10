from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

KnowledgeSourceType = Literal["url", "workspace_file"]
KnowledgeSourceStatus = Literal["active", "paused", "archived"]


@dataclass(frozen=True, slots=True)
class KnowledgeSourceCreate:
    name: str
    description: str
    source_type: KnowledgeSourceType
    uri: str | None
    workspace_file_id: UUID | None
    config: dict[str, object]


@dataclass(frozen=True, slots=True)
class KnowledgeSourceUpdate:
    expected_version: int
    name: str | None
    description: str | None
    source_type: KnowledgeSourceType | None
    uri: str | None
    workspace_file_id: UUID | None
    config: dict[str, object] | None
    status: Literal["active", "paused"] | None
