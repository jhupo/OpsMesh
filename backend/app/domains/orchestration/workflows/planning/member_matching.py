from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.utils import string_list, uuid_or_none
from backend.app.domains.orchestration.runs.models import AgentRun
from backend.app.domains.orchestration.tasks.models import TaskStep
from backend.app.domains.orchestration.workflows.planning.org_structure import (
    is_leadership_role,
    normalize_role,
)
from backend.app.domains.orchestration.workflows.statuses import WORKLOAD_RUN_STATUS_VALUES
from backend.app.domains.orchestration.workflows.templates.members import (
    member_agent_profile_id,
    member_department,
    member_role,
    request_department,
)
from backend.app.domains.orchestration.workflows.templates.normalization import same_label


@dataclass(frozen=True)
class MemberMatch:
    agent_profile_id: UUID
    team_member_id: UUID | None
    team_role: str
    score: float
    reasons: tuple[str, ...]
    current_load: int
    max_concurrent_tasks: int


class MemberMatchingService:
    def __init__(self, session: Session | None = None) -> None:
        self._session = session

    def match(
        self,
        *,
        team_snapshot: dict[str, object],
        required_role: str,
        required_skills: list[str],
        workspace_id: UUID | None = None,
    ) -> MemberMatch | None:
        candidates = self.rank(
            team_snapshot=team_snapshot,
            required_role=required_role,
            required_skills=required_skills,
            workspace_id=workspace_id,
        )
        return candidates[0] if candidates else None

    def rank(
        self,
        *,
        team_snapshot: dict[str, object],
        required_role: str,
        required_skills: list[str],
        workspace_id: UUID | None = None,
    ) -> list[MemberMatch]:
        matches: list[MemberMatch] = []
        for member in _snapshot_members(team_snapshot):
            if member.get("accepts_tasks") is False:
                continue

            agent_profile_id = uuid_or_none(member.get("agent_profile_id"))
            if agent_profile_id is None:
                continue

            max_concurrent_tasks = max(_int_or_default(member.get("max_concurrent_tasks"), 1), 1)
            current_load = self._current_load(
                workspace_id=workspace_id,
                agent_profile_id=agent_profile_id,
            )
            if current_load >= max_concurrent_tasks:
                continue

            team_role = str(member.get("team_role") or "")
            score, reasons = _score_member(
                member=member,
                required_role=required_role,
                required_skills=required_skills,
            )
            load_penalty = current_load / max_concurrent_tasks
            score -= load_penalty
            if current_load:
                reasons.append(f"load_penalty:{load_penalty:.2f}")

            matches.append(
                MemberMatch(
                    agent_profile_id=agent_profile_id,
                    team_member_id=uuid_or_none(member.get("id")),
                    team_role=team_role,
                    score=score,
                    reasons=tuple(reasons),
                    current_load=current_load,
                    max_concurrent_tasks=max_concurrent_tasks,
                )
            )

        return sorted(
            matches,
            key=lambda match: (-match.score, match.current_load, match.team_role),
        )

    def _current_load(self, *, workspace_id: UUID | None, agent_profile_id: UUID) -> int:
        if self._session is None or workspace_id is None:
            return 0
        return int(
            self._session.scalar(
                select(func.count(AgentRun.id))
                .join(TaskStep, TaskStep.id == AgentRun.task_step_id)
                .where(
                    AgentRun.workspace_id == workspace_id,
                    AgentRun.agent_profile_id == agent_profile_id,
                    AgentRun.status.in_(WORKLOAD_RUN_STATUS_VALUES),
                    TaskStep.status.in_(["queued", "running"]),
                )
            )
            or 0
        )


def _score_member(
    *,
    member: dict[str, object],
    required_role: str,
    required_skills: list[str],
) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    role = str(member.get("team_role") or "").lower()
    normalized_role = normalize_role(role)
    normalized_required_role = normalize_role(required_role)
    if normalized_required_role and role == normalized_required_role:
        score += 100
        reasons.append("role_exact")
    elif normalized_required_role and (
        normalized_required_role in role or role in normalized_required_role
    ):
        score += 60
        reasons.append("role_partial")

    skill_weights = member.get("skill_weights")
    if isinstance(skill_weights, dict):
        for skill in required_skills:
            raw_weight = skill_weights.get(skill)
            weight = raw_weight if isinstance(raw_weight, int | float) else 0
            if weight > 0:
                score += float(weight) * 25
                reasons.append(f"skill:{skill}")

    responsibilities = " ".join(string_list(member.get("responsibilities"))).lower()
    for skill in required_skills:
        if skill.lower() in responsibilities:
            score += 10
            reasons.append(f"responsibility:{skill}")

    department = str(member.get("department") or "").lower()
    if department and normalized_required_role and normalized_required_role in department:
        score += 12
        reasons.append("department_match")

    if (
        is_leadership_role(normalized_role)
        and normalized_required_role
        and normalized_required_role != normalized_role
    ):
        score -= 80
        reasons.append("leadership_execution_penalty")

    if member.get("is_required") is True:
        score += 2
        reasons.append("required_member")

    return score, reasons


def _snapshot_members(snapshot: dict[str, object]) -> list[dict[str, object]]:
    raw_members = snapshot.get("members", [])
    if not isinstance(raw_members, list):
        return []
    return [member for member in raw_members if isinstance(member, dict)]


def _int_or_default(value: object, default: int) -> int:
    if isinstance(value, int):
        return value
    return default


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
