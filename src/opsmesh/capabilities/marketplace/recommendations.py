from __future__ import annotations

from dataclasses import dataclass

from opsmesh.capabilities.marketplace.contracts import TalentRecommendationRequest
from opsmesh.capabilities.marketplace.models import TalentListing
from opsmesh.orchestration.tasks.models import Task
from opsmesh.shared.utils import string_list, string_or_default


@dataclass(frozen=True)
class RoleSpec:
    role: str
    team_role: str
    reason: str
    skill_tags: tuple[str, ...]
    capability_tags: tuple[str, ...]


@dataclass(frozen=True)
class ScoredListing:
    listing: TalentListing
    score: float
    reasons: list[str]
    missing_tags: list[str]


def role_specs_for_request(data: TalentRecommendationRequest) -> list[RoleSpec]:
    # Roles are supplied by the caller (including a planning agent), never inferred
    # from a built-in industry/team-type list.
    if not data.required_roles:
        raise ValueError("Specify the roles required by this request")
    return [
        RoleSpec(
            role=normalize_role(role),
            team_role=role.strip(),
            reason="Requested role",
            skill_tags=tuple(normalize_tag(tag) for tag in data.skill_tags if tag),
            capability_tags=tuple(normalize_tag(tag) for tag in data.capability_tags if tag),
        )
        for role in data.required_roles
    ]


def missing_work_packages_from_task(task: Task) -> list[dict[str, object]]:
    project_plan = task.project_plan if isinstance(task.project_plan, dict) else None
    if project_plan is None:
        return []
    packages = project_plan.get("work_packages", [])
    if not isinstance(packages, list):
        return []

    missing: list[dict[str, object]] = []
    for raw_package in packages:
        if not isinstance(raw_package, dict):
            continue
        if raw_package.get("assigned_agent_profile_id") is not None:
            continue
        role = string_or_default(raw_package.get("required_role"), "specialist")
        missing.append(
            {
                "package_id": string_or_default(raw_package.get("package_id"), "unknown"),
                "title": string_or_default(raw_package.get("title"), role),
                "required_role": role,
                "required_skills": string_list(raw_package.get("required_skills")),
                "expected_artifacts": string_list(raw_package.get("expected_artifacts")),
            }
        )
    return missing


def missing_work_package_by_id(task: Task, work_package_id: str) -> dict[str, object] | None:
    return next(
        (
            package
            for package in missing_work_packages_from_task(task)
            if package.get("package_id") == work_package_id
        ),
        None,
    )


def role_spec_for_missing_package(package: dict[str, object]) -> RoleSpec:
    role = string_or_default(package.get("required_role"), "specialist")
    return RoleSpec(
        role=normalize_role(role),
        team_role=role,
        reason=f"Work package {package.get('package_id', 'unknown')} has no assigned agent.",
        skill_tags=tuple(
            normalize_tag(skill) for skill in string_list(package.get("required_skills"))
        ),
        capability_tags=(),
    )


def task_team_type(task: Task) -> str:
    snapshot = task.team_snapshot if isinstance(task.team_snapshot, dict) else None
    if snapshot is None:
        return "general"
    team = snapshot.get("team")
    if not isinstance(team, dict):
        return "general"
    return string_or_default(team.get("team_type"), "general")


def score_listing(
    listing: TalentListing,
    *,
    role: str,
    skill_tags: tuple[str, ...],
    capability_tags: tuple[str, ...],
) -> ScoredListing:
    score = 0.0
    reasons: list[str] = []
    missing_tags: list[str] = []
    listing_skills = {normalize_tag(tag) for tag in listing.skill_tags}
    listing_capabilities = {normalize_tag(tag) for tag in listing.capability_tags}
    normalized_role = normalize_role(listing.role)

    if normalized_role == role:
        score += 5
        reasons.append("岗位匹配")
    elif role in normalized_role or normalized_role in role:
        score += 2
        reasons.append("岗位相近")

    for tag in skill_tags:
        if tag in listing_skills:
            score += 1.5
            reasons.append(f"技能匹配: {tag}")
        else:
            missing_tags.append(tag)

    for tag in capability_tags:
        if tag in listing_capabilities:
            score += 1
            reasons.append(f"能力匹配: {tag}")
        else:
            missing_tags.append(tag)

    if listing.risk_level == "low":
        score += 0.25
        reasons.append("低风险")

    return ScoredListing(
        listing=listing,
        score=score,
        reasons=reasons,
        missing_tags=list(dict.fromkeys(missing_tags)),
    )


def normalize_role(value: str) -> str:
    return value.strip().lower().replace(" ", "_").replace("-", "_")


def normalize_tag(value: str) -> str:
    return value.strip().lower()
