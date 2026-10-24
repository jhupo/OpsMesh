from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

DependencyAlias = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]


class DependencyReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["plugin", "agent", "tool", "mcp", "skill"]
    id: str = Field(min_length=1, max_length=200)
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$", max_length=60)


class PluginManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$", max_length=60)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=4000)
    dependencies: dict[DependencyAlias, DependencyReference] = Field(
        default_factory=dict, max_length=50
    )
