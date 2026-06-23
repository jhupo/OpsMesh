from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class ProjectWorkPackage:
    package_id: str
    title: str
    description: str
    required_role: str
    required_skills: tuple[str, ...]
    assigned_agent_profile_id: UUID | None
    depends_on: tuple[str, ...]
    expected_artifacts: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    review_policy: dict[str, object]

    def as_dict(self) -> dict[str, object]:
        return {
            "package_id": self.package_id,
            "title": self.title,
            "description": self.description,
            "required_role": self.required_role,
            "required_skills": list(self.required_skills),
            "assigned_agent_profile_id": str(self.assigned_agent_profile_id)
            if self.assigned_agent_profile_id is not None
            else None,
            "depends_on": list(self.depends_on),
            "expected_artifacts": list(self.expected_artifacts),
            "acceptance_criteria": list(self.acceptance_criteria),
            "review_policy": self.review_policy,
        }


@dataclass(frozen=True)
class ProjectPlan:
    plan_id: str
    objective: str
    planner_agent_profile_id: UUID | None
    generated_at: str
    strategy: str
    work_packages: tuple[ProjectWorkPackage, ...]

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
            "work_packages": [package.as_dict() for package in self.work_packages],
        }
