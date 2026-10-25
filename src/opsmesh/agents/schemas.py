from typing import Any

from pydantic import Field

from opsmesh.contracts.manifest import DependencyAlias, DependencyReference
from opsmesh.shared.schemas import RecordView, RequestSchema


class CreateAgentRequest(RequestSchema):
    name: str = Field(min_length=1, max_length=100)
    plugin_id: str
    entry: str = Field(min_length=1, max_length=64)
    config: dict[str, Any] = Field(default_factory=dict)
    dependencies: dict[DependencyAlias, DependencyReference] = Field(
        default_factory=dict, max_length=50
    )
    workspace_id: str | None = None


class UpdateAgentRequest(RequestSchema):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    enabled: bool | None = None
    config: dict[str, Any] | None = None
    dependencies: dict[DependencyAlias, DependencyReference] | None = Field(
        default=None, max_length=50
    )
    workspace_id: str | None = None


class AgentView(RecordView):
    owner_id: str
    name: str
    plugin_id: str
    entry: str
    enabled: bool


class AgentDetailsView(AgentView):
    config: dict[str, Any] | None = None
    workspace_id: str | None = None
    dependencies: dict[str, Any] | None = None
