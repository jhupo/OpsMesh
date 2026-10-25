from typing import Any, Literal

from pydantic import Field

from opsmesh.contracts.manifest import DependencyAlias, DependencyReference
from opsmesh.shared.schemas import RecordView, RequestSchema


class PublishResourceRequest(RequestSchema):
    name: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,99}$")
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$", max_length=60)
    kind: Literal["plugin", "agent", "tool", "mcp", "skill"]
    plugin_id: str | None = None
    agent_id: str | None = None
    contribution: str = Field(default="", max_length=64)
    description: str = Field(default="", max_length=4000)
    visibility: Literal["private", "group", "public"] = "private"
    group_ids: list[str] = Field(default_factory=list, max_length=100)
    config: dict[str, Any] = Field(default_factory=dict)
    dependencies: dict[DependencyAlias, DependencyReference] = Field(
        default_factory=dict, max_length=50
    )


class PublicationView(RecordView):
    owner_id: str
    name: str
    version: str
    kind: str
    plugin_id: str
    contribution: str
    description: str
    visibility: str
    group_ids: list[str]
    dependencies: dict[str, Any]
    enabled: bool
