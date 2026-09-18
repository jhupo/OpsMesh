"""Editor metadata and discoverable node contracts, independent of a UI framework."""

from typing import get_args

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat

from backend.app.domains.orchestration.workflows.definitions.contracts import (
    WorkflowNode,
    WorkflowNodeType,
)


class NodePosition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: FiniteFloat = Field(ge=-1_000_000, le=1_000_000)
    y: FiniteFloat = Field(ge=-1_000_000, le=1_000_000)


class WorkflowEditorMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    positions: dict[str, NodePosition] = Field(default_factory=dict, max_length=128)
    viewport: NodePosition = Field(default_factory=lambda: NodePosition(x=0, y=0))
    zoom: FiniteFloat = Field(default=1, ge=0.1, le=4)

    def validate_nodes(self, nodes: list[WorkflowNode]) -> None:
        unknown = set(self.positions) - {node.package_id for node in nodes}
        if unknown:
            raise ValueError("Editor positions must reference existing node IDs")


class NodeDescriptor(BaseModel):
    node_type: WorkflowNodeType
    input_port: str = "input"
    output_port: str = "output"
    required_fields: list[str]


class WorkflowAuthoringContract(BaseModel):
    node_schema: dict[str, object]
    editor_schema: dict[str, object]
    nodes: list[NodeDescriptor]
    selectors: dict[str, str]
    dependency_field: str = "depends_on"
    binding_field: str = "input_bindings"


def authoring_contract() -> WorkflowAuthoringContract:
    requirements = {
        "tool": ["tool_name"],
        "mcp": ["tool_name", "required_mcp_tools"],
        "subworkflow": ["subworkflow_definition_id"],
    }
    return WorkflowAuthoringContract(
        node_schema=WorkflowNode.model_json_schema(),
        editor_schema=WorkflowEditorMetadata.model_json_schema(),
        nodes=[
            NodeDescriptor(
                node_type=kind,
                required_fields=["package_id", "title", *requirements.get(kind, [])],
            )
            for kind in get_args(WorkflowNodeType)
        ],
        selectors={
            "assigned_agent_profile_id": "agent_profile",
            "required_resource_ids": "workspace_resource",
            "required_mcp_tools": "authorized_mcp_tool",
            "required_tools": "authorized_product_tool",
            "subworkflow_definition_id": "published_orchestration",
        },
    )
