"""Versioned deployment-owned prompt templates without provider SDK imports."""

from typing import Any

from pydantic import Field

from opsmesh.framework.typing import OpsMeshContract


class PromptTemplateDefinition(OpsMeshContract):
    id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")
    revision: int = Field(default=1, ge=1)
    remote_id: str = Field(min_length=1)
    remote_version: str = Field(min_length=1)
    defaults: dict[str, Any] = Field(default_factory=dict)
    required_variables: tuple[str, ...] = ()
    allowed_variables: tuple[str, ...] | None = None

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.revision}"
