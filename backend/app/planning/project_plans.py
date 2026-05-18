from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from backend.app.tasks.models import Task


class ProjectPlanValidationError(ValueError):
    pass


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


class ProjectPlanningService:
    def create_initial_plan(self, task: Task) -> dict[str, object] | None:
        if not isinstance(task.team_snapshot, dict):
            return None
        snapshot = task.team_snapshot
        team = snapshot.get("team")
        if not isinstance(team, dict):
            return None

        manager_agent_profile_id = _uuid_or_none(team.get("manager_agent_profile_id"))
        members = _snapshot_members(snapshot)
        if manager_agent_profile_id is None and not members:
            return None

        work_packages: list[ProjectWorkPackage] = []
        manager_package_id: str | None = None
        if manager_agent_profile_id is not None:
            manager_package_id = "manager-planning"
            work_packages.append(
                ProjectWorkPackage(
                    package_id=manager_package_id,
                    title="Manager planning",
                    description=(
                        "Clarify the goal, split responsibilities, and prepare the team plan."
                    ),
                    required_role="project_manager",
                    required_skills=("planning", "coordination"),
                    assigned_agent_profile_id=manager_agent_profile_id,
                    depends_on=(),
                    expected_artifacts=("project_plan",),
                    acceptance_criteria=("The team has a clear execution plan.",),
                    review_policy={"reviewer": "manager", "mode": "self_review"},
                )
            )

        specialist_package_ids: list[str] = []
        for index, member in enumerate(members, start=1):
            agent_profile_id = _uuid_or_none(member.get("agent_profile_id"))
            if agent_profile_id is None:
                continue
            role = str(member.get("team_role") or "specialist")
            package_id = f"{role}-{index}"
            specialist_package_ids.append(package_id)
            work_packages.append(
                ProjectWorkPackage(
                    package_id=package_id,
                    title=f"{role} execution",
                    description=f"Complete the assigned {role} work package for the task.",
                    required_role=role,
                    required_skills=tuple(_skill_names(member.get("skill_weights"))),
                    assigned_agent_profile_id=agent_profile_id,
                    depends_on=(manager_package_id,) if manager_package_id is not None else (),
                    expected_artifacts=tuple(_expected_artifacts_for_role(role)),
                    acceptance_criteria=(
                        "The work package produces a clear result summary.",
                        "Any generated files or artifacts are attached to the task.",
                    ),
                    review_policy={"reviewer": "manager", "mode": "manager_review"},
                )
            )

        if manager_agent_profile_id is not None and specialist_package_ids:
            work_packages.append(
                ProjectWorkPackage(
                    package_id="manager-summary",
                    title="Manager summary",
                    description=(
                        "Review specialist outputs, reconcile issues, and produce the final answer."
                    ),
                    required_role="project_manager",
                    required_skills=("review", "synthesis"),
                    assigned_agent_profile_id=manager_agent_profile_id,
                    depends_on=tuple(specialist_package_ids),
                    expected_artifacts=("final_delivery",),
                    acceptance_criteria=(
                        "The final answer integrates all completed work packages.",
                    ),
                    review_policy={"reviewer": "user", "mode": "final_acceptance"},
                )
            )

        plan = ProjectPlan(
            plan_id=str(uuid4()),
            objective=task.title,
            planner_agent_profile_id=manager_agent_profile_id,
            generated_at=datetime.now(UTC).isoformat(),
            strategy="deterministic_team_snapshot_v1",
            work_packages=tuple(work_packages),
        ).as_dict()
        validate_project_plan(plan, task.team_snapshot)
        return plan


def validate_project_plan(
    plan: dict[str, object],
    team_snapshot: dict[str, object] | None,
) -> None:
    if not isinstance(team_snapshot, dict):
        raise ProjectPlanValidationError("Project plan requires a team snapshot")
    packages = plan.get("work_packages")
    if not isinstance(packages, list) or not packages:
        raise ProjectPlanValidationError("Project plan must include work packages")

    allowed_agent_ids = _snapshot_agent_ids(team_snapshot)
    package_ids: set[str] = set()
    for raw_package in packages:
        if not isinstance(raw_package, dict):
            raise ProjectPlanValidationError("Work package must be an object")
        package_id = _required_string(raw_package, "package_id")
        if package_id in package_ids:
            raise ProjectPlanValidationError(f"Duplicate work package id: {package_id}")
        package_ids.add(package_id)
        _required_string(raw_package, "title")
        _required_string(raw_package, "required_role")

        agent_id = raw_package.get("assigned_agent_profile_id")
        if agent_id is not None and str(agent_id) not in allowed_agent_ids:
            raise ProjectPlanValidationError("Work package assigned agent is not in team snapshot")

    for raw_package in packages:
        if not isinstance(raw_package, dict):
            continue
        depends_on = raw_package.get("depends_on", [])
        if not isinstance(depends_on, list):
            raise ProjectPlanValidationError("Work package dependencies must be a list")
        for dependency in depends_on:
            if str(dependency) not in package_ids:
                raise ProjectPlanValidationError(
                    f"Unknown work package dependency: {dependency}"
                )


def _snapshot_members(snapshot: dict[str, object]) -> list[dict[str, object]]:
    raw_members = snapshot.get("members", [])
    if not isinstance(raw_members, list):
        return []
    members = [member for member in raw_members if isinstance(member, dict)]
    return sorted(
        members,
        key=lambda member: (
            _int_or_default(member.get("order_index"), 0),
            str(member.get("team_role") or ""),
        ),
    )


def _snapshot_agent_ids(snapshot: dict[str, object]) -> set[str]:
    team = snapshot.get("team") if isinstance(snapshot.get("team"), dict) else {}
    ids = {
        str(member["agent_profile_id"])
        for member in _snapshot_members(snapshot)
        if member.get("agent_profile_id") is not None
    }
    if isinstance(team, dict) and team.get("manager_agent_profile_id") is not None:
        ids.add(str(team["manager_agent_profile_id"]))
    return ids


def _skill_names(value: object) -> list[str]:
    if not isinstance(value, dict):
        return []
    return [str(key) for key in value if isinstance(key, str)]


def _expected_artifacts_for_role(role: str) -> list[str]:
    normalized = role.lower()
    if "design" in normalized or "ui" in normalized:
        return ["design_artifact"]
    if "developer" in normalized or "engineer" in normalized:
        return ["implementation_artifact"]
    if "qa" in normalized or "test" in normalized:
        return ["test_report"]
    return ["work_summary"]


def _required_string(value: dict[str, object], key: str) -> str:
    raw_value = value.get(key)
    if not isinstance(raw_value, str) or not raw_value:
        raise ProjectPlanValidationError(f"Work package missing required field: {key}")
    return raw_value


def _uuid_or_none(value: object | None) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(str(value))
    except ValueError:
        return None


def _int_or_default(value: object, default: int) -> int:
    if isinstance(value, int):
        return value
    return default
