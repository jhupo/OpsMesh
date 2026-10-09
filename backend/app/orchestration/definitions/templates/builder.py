"""Explicit user-authored plans; no organization or role presets."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from backend.app.orchestration.definitions.contracts import WorkflowNode
from backend.app.orchestration.definitions.graph import (
    ProjectPlanValidationError,
    validate_project_plan,
)
from backend.app.orchestration.tasks.models import Task
from backend.app.shared.utils import uuid_or_none


class PlanningContext:
    def __init__(self, planner_agent_profile_id: UUID | None) -> None:
        self.planner_agent_profile_id = planner_agent_profile_id

    @classmethod
    def from_task(cls, task: Task) -> PlanningContext | None:
        snapshot = task.team_snapshot
        if not isinstance(snapshot, dict):
            return None
        team = snapshot.get("team")
        if not isinstance(team, dict):
            return None
        return cls(uuid_or_none(team.get("manager_agent_profile_id")))


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
        "input_bindings",
        "subworkflow_definition_id",
        "subworkflow_version",
    ):
        value = payload.get(key)
        if value in (None, False, 0, {}, [], "all_success", "agent"):
            payload.pop(key, None)
    return payload


class ProjectPlanningService:
    def create_initial_plan(self, task: Task) -> dict[str, object]:
        raw = (task.input or {}).get("work_packages")
        context = PlanningContext.from_task(task)
        if (task.input or {}).get("planning_mode", "direct") == "direct":
            if context is None or context.planner_agent_profile_id is None:
                raise ProjectPlanValidationError("Team execution requires an assigned entry agent")
            raw = [
                {
                    "package_id": "entry",
                    "title": task.title,
                    "description": task.description or "",
                    "assigned_agent_profile_id": str(context.planner_agent_profile_id),
                }
            ]
        if not isinstance(raw, list) or not raw:
            raise ProjectPlanValidationError("Explicit planning requires work_packages")
        plan = ProjectPlan(
            plan_id=str(uuid4()),
            objective=task.title,
            planner_agent_profile_id=context.planner_agent_profile_id if context else None,
            generated_at=datetime.now(UTC).isoformat(),
            strategy="direct"
            if (task.input or {}).get("planning_mode", "direct") == "direct"
            else "explicit_workflow",
            work_packages=tuple(WorkflowNode.model_validate(item) for item in raw),
        ).as_dict()
        validate_project_plan(plan, task.team_snapshot)
        return plan
