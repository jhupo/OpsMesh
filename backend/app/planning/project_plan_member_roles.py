from __future__ import annotations

from uuid import UUID

from backend.app.planning.project_plan_utils import string_or_default, uuid_or_none


def member_agent_profile_ids(members: list[dict[str, object]]) -> set[UUID]:
    return {
        agent_profile_id
        for member in members
        if (agent_profile_id := member_agent_profile_id(member)) is not None
    }


def first_member_agent_profile_id(members: list[dict[str, object]]) -> UUID | None:
    for member in members:
        agent_profile_id = member_agent_profile_id(member)
        if agent_profile_id is not None:
            return agent_profile_id
    return None


def member_agent_profile_id(member: dict[str, object] | None) -> UUID | None:
    if member is None:
        return None
    return uuid_or_none(member.get("agent_profile_id"))


def member_key(member: dict[str, object]) -> str:
    raw_id = member.get("id")
    if isinstance(raw_id, str) and raw_id:
        return f"id:{raw_id}"
    agent_profile_id = member_agent_profile_id(member)
    if agent_profile_id is not None:
        return f"agent:{agent_profile_id}"
    return f"role:{member_role(member, 'member')}:{member_department(member)}"


def member_role(member: dict[str, object], default: str) -> str:
    return string_or_default(member.get("team_role"), default)


def member_department(member: dict[str, object] | None) -> str | None:
    if member is None:
        return None
    department = member.get("department")
    return department if isinstance(department, str) and department else None


def member_position_title(member: dict[str, object]) -> str:
    return string_or_default(member.get("position_title"), "")


def is_executive_role(member: dict[str, object]) -> bool:
    text = role_text(member)
    return any(token in text for token in ("ceo", "cto", "chief executive", "chief technology"))


def is_manager_role(member: dict[str, object]) -> bool:
    text = role_text(member)
    if any(
        token in text
        for token in ("project_manager", "product_manager", "program_manager", "manager")
    ):
        return True
    return "pm" in role_tokens(member)


def is_lead_role(member: dict[str, object]) -> bool:
    text = role_text(member)
    return any(token in text for token in ("team_lead", "tech_lead", "lead", "head of"))


def role_text(member: dict[str, object]) -> str:
    return " ".join(
        value.lower().replace("-", "_")
        for value in (
            member_role(member, ""),
            member_department(member) or "",
            member_position_title(member),
        )
        if value
    )


def role_tokens(member: dict[str, object]) -> set[str]:
    tokens: set[str] = set()
    for value in (
        member_role(member, ""),
        member_department(member) or "",
        member_position_title(member),
    ):
        normalized = value.lower().replace("-", "_")
        tokens.update(part for part in normalized.replace("_", " ").split() if part)
    return tokens
