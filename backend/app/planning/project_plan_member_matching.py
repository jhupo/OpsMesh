from __future__ import annotations

from uuid import UUID

from backend.app.planning.project_plan_member_roles import (
    member_agent_profile_id,
    member_department,
    member_role,
)
from backend.app.planning.project_plan_utils import same_label


def requested_member_match(
    *,
    members: list[dict[str, object]],
    role: str,
    required_skills: list[str],
    request: dict[str, object],
    matcher_agent_profile_id: UUID | None,
) -> dict[str, object] | None:
    if "assigned_agent_profile_id" in request:
        requested_agent_id = _requested_agent_id(request)
        if requested_agent_id is None:
            return None
        return next(
            (
                member
                for member in members
                if member_agent_profile_id(member) == requested_agent_id
            ),
            None,
        )
    if matcher_agent_profile_id is not None:
        for member in members:
            if member_agent_profile_id(member) == matcher_agent_profile_id:
                return member
    return _best_local_member_match(
        members=_department_filtered_members(members, request),
        role=role,
        required_skills=required_skills,
    )


def _requested_agent_id(request: dict[str, object]) -> UUID | None:
    raw_id = request.get("assigned_agent_profile_id")
    if raw_id is None:
        return None
    try:
        return UUID(str(raw_id))
    except (TypeError, ValueError):
        return None


def request_department(request: dict[str, object]) -> str | None:
    for key in ("department", "required_department", "team", "area"):
        value = request.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _department_filtered_members(
    members: list[dict[str, object]],
    request: dict[str, object],
) -> list[dict[str, object]]:
    requested_department = request_department(request)
    if requested_department is None:
        return members
    department_matches = [
        member
        for member in members
        if (department := member_department(member)) is not None
        and same_label(department, requested_department)
    ]
    return department_matches or members


def _best_local_member_match(
    *,
    members: list[dict[str, object]],
    role: str,
    required_skills: list[str],
) -> dict[str, object] | None:
    scored: list[tuple[float, str, dict[str, object]]] = []
    for member in members:
        score = _role_score(member_role(member, ""), role) + _skill_score(member, required_skills)
        scored.append((score, member_role(member, ""), member))
    if not scored:
        return None
    scored.sort(key=lambda item: (-item[0], item[1]))
    return scored[0][2]


def _role_score(member_role_value: str, requested_role: str) -> float:
    member_role_text = member_role_value.lower()
    normalized_role = requested_role.lower()
    if normalized_role and member_role_text == normalized_role:
        return 100
    if normalized_role and (
        normalized_role in member_role_text or member_role_text in normalized_role
    ):
        return 60
    return 0


def _skill_score(member: dict[str, object], required_skills: list[str]) -> float:
    skill_weights = member.get("skill_weights")
    if not isinstance(skill_weights, dict):
        return 0
    score = 0.0
    for skill in required_skills:
        raw_weight = skill_weights.get(skill)
        weight = raw_weight if isinstance(raw_weight, int | float) else 0
        score += float(weight) * 25
    return score
