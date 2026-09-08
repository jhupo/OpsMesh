from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ProjectCreateCommand:
    name: str
    slug: str
    description: str = ""
    input_path: str = "inputs"
    work_path: str = "work"
    output_path: str = "outputs"
    configuration: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProjectUpdateCommand:
    name: str | None = None
    description: str | None = None
    input_path: str | None = None
    work_path: str | None = None
    output_path: str | None = None
    configuration: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class ProjectFileCommand:
    workspace_file_id: UUID
    project_path: str
    access_mode: str = "read_only"


@dataclass(frozen=True, slots=True)
class ProjectOutputCommand:
    project_path: str
    artifact_type: str
    content_type: str | None
    required: bool
    max_bytes: int
