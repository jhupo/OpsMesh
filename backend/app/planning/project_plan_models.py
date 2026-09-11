from dataclasses import dataclass
from uuid import UUID

from backend.app.planning.workflow_contracts import WorkflowNode


@dataclass(frozen=True)
class ProjectPlan:
    plan_id: str
    objective: str
    planner_agent_profile_id: UUID | None
    generated_at: str
    strategy: str
    work_packages: tuple[WorkflowNode, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "plan_version": 1,
            "plan_id": self.plan_id,
            "objective": self.objective,
            "planner_agent_profile_id": str(self.planner_agent_profile_id)
            if self.planner_agent_profile_id is not None
            else None,
            "generated_at": self.generated_at,
            "strategy": self.strategy,
            "work_packages": [_package_dict(package) for package in self.work_packages],
        }


def _package_dict(package: WorkflowNode) -> dict[str, object]:
    payload = package.model_dump(mode="json", by_alias=True, exclude_none=True)
    for key in (
        "node_type",
        "required_tools",
        "required_mcp_tools",
        "required_resource_ids",
        "resource_requirements",
        "estimated_cost_usd",
        "join_policy",
        "locked",
        "tool_name",
        "arguments",
        "output_schema",
        "subworkflow_definition_id",
    ):
        value = payload.get(key)
        if value in (None, False, 0, {}, [], "all_success", "agent"):
            payload.pop(key, None)
    return payload
