from __future__ import annotations

from uuid import UUID

from backend.app.domains.orchestration.workflows.templates.normalization import (
    int_or_default,
    same_label,
    string_or_default,
    uuid_or_none,
)


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


def request_department(request: dict[str, object]) -> str | None:
    for key in ("department", "required_department", "team", "area"):
        value = request.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def snapshot_members(snapshot: dict[str, object]) -> list[dict[str, object]]:
    raw_members = snapshot.get("members", [])
    if not isinstance(raw_members, list):
        return []
    members = [member for member in raw_members if isinstance(member, dict)]
    return sorted(
        members,
        key=lambda member: (
            int_or_default(member.get("order_index"), 0),
            str(member.get("team_role") or ""),
        ),
    )


def snapshot_agent_ids(snapshot: dict[str, object]) -> set[str]:
    team = snapshot.get("team") if isinstance(snapshot.get("team"), dict) else {}
    ids = {
        str(member["agent_profile_id"])
        for member in snapshot_members(snapshot)
        if member.get("agent_profile_id") is not None
    }
    if isinstance(team, dict) and team.get("manager_agent_profile_id") is not None:
        ids.add(str(team["manager_agent_profile_id"]))
    return ids


def executive_members(members: list[dict[str, object]]) -> list[dict[str, object]]:
    return [member for member in members if is_executive_role(member)]


def manager_member(
    members: list[dict[str, object]],
    manager_agent_profile_id: UUID | None,
) -> dict[str, object] | None:
    for member in members:
        if (
            manager_agent_profile_id is not None
            and member_agent_profile_id(member) == manager_agent_profile_id
            and not is_executive_role(member)
        ):
            return member
    for member in members:
        if is_manager_role(member) and not is_executive_role(member):
            return member
    return None


def lead_members(
    members: list[dict[str, object]],
    manager_agent_profile_id: UUID | None,
) -> list[dict[str, object]]:
    return [
        member
        for member in members
        if is_lead_role(member)
        and not is_executive_role(member)
        and not is_manager_role(member)
        and member_agent_profile_id(member) != manager_agent_profile_id
    ]


def execution_members(
    members: list[dict[str, object]],
    *,
    executives: list[dict[str, object]],
    manager: dict[str, object] | None,
    manager_agent_profile_id: UUID | None,
    leads: list[dict[str, object]],
) -> list[dict[str, object]]:
    excluded_keys = {member_key(member) for member in executives + leads}
    if manager is not None:
        excluded_keys.add(member_key(manager))
    excluded_agent_ids = member_agent_profile_ids(executives + leads)
    if manager_agent_profile_id is not None:
        excluded_agent_ids.add(manager_agent_profile_id)
    execution = [
        member
        for member in members
        if member_key(member) not in excluded_keys
        and member_agent_profile_id(member) not in excluded_agent_ids
        and not is_executive_role(member)
    ]
    if execution or not leads:
        return execution
    return [
        member
        for member in members
        if member_key(member) not in excluded_keys and not is_executive_role(member)
    ]


def lead_package_for_member(
    *,
    member: dict[str, object],
    leads: list[dict[str, object]],
    lead_package_ids: dict[str, str],
) -> str | None:
    lead = lead_for_member(member=member, leads=leads)
    if lead is None:
        return None
    return lead_package_ids.get(member_key(lead))


def lead_package_for_request(
    *,
    request: dict[str, object],
    matched_member: dict[str, object] | None,
    leads: list[dict[str, object]],
    lead_package_ids: dict[str, str],
) -> str | None:
    if matched_member is not None:
        return lead_package_for_member(
            member=matched_member,
            leads=leads,
            lead_package_ids=lead_package_ids,
        )
    requested_department = request_department(request)
    if requested_department is None:
        return None
    for lead in leads:
        lead_department = member_department(lead)
        if lead_department is not None and same_label(lead_department, requested_department):
            return lead_package_ids.get(member_key(lead))
    return None


def lead_for_member(
    *,
    member: dict[str, object],
    leads: list[dict[str, object]],
) -> dict[str, object] | None:
    reports_to_member_id = member.get("reports_to_member_id")
    if isinstance(reports_to_member_id, str) and reports_to_member_id:
        for lead in leads:
            if lead.get("id") == reports_to_member_id:
                return lead
    department = member_department(member)
    if department is not None:
        for lead in leads:
            lead_department = member_department(lead)
            if lead_department is not None and same_label(lead_department, department):
                return lead
    return leads[0] if len(leads) == 1 else None


__all__ = [
    "executive_members",
    "execution_members",
    "first_member_agent_profile_id",
    "is_executive_role",
    "is_lead_role",
    "is_manager_role",
    "lead_members",
    "lead_package_for_member",
    "lead_package_for_request",
    "manager_member",
    "member_agent_profile_id",
    "member_agent_profile_ids",
    "member_department",
    "member_key",
    "member_position_title",
    "member_role",
    "request_department",
    "role_text",
    "role_tokens",
    "snapshot_agent_ids",
    "snapshot_members",
]
