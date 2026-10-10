from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from uuid import UUID

from opsmesh.shared.utils import non_empty_string_or_none, string_list, uuid_or_none


@dataclass(frozen=True)
class OrgMember:
    id: str
    agent_profile_id: UUID | None
    reports_to_member_id: str | None
    team_role: str
    normalized_role: str
    department: str | None
    position_title: str | None
    responsibilities: tuple[str, ...]
    order_index: int
    raw: dict[str, object]


@dataclass(frozen=True)
class OrgStructure:
    members: tuple[OrgMember, ...]
    executives: tuple[OrgMember, ...]
    managers: tuple[OrgMember, ...]
    leads: tuple[OrgMember, ...]
    contributors: tuple[OrgMember, ...]
    departments: dict[str, tuple[OrgMember, ...]]
    children_by_member_id: dict[str, tuple[OrgMember, ...]]
    cycle_member_ids: tuple[str, ...]
    orphan_member_ids: tuple[str, ...]

    @property
    def primary_manager(self) -> OrgMember | None:
        return self.managers[0] if self.managers else None


def build_org_structure(snapshot: dict[str, object] | None) -> OrgStructure:
    members = tuple(_org_members(snapshot))
    by_id = {member.id: member for member in members}
    children: dict[str, list[OrgMember]] = defaultdict(list)
    orphan_ids: list[str] = []
    for member in members:
        manager_id = member.reports_to_member_id
        if manager_id is None:
            continue
        parent = by_id.get(manager_id)
        if parent is None:
            orphan_ids.append(member.id)
            continue
        children[parent.id].append(member)

    departments: dict[str, list[OrgMember]] = defaultdict(list)
    for member in members:
        if member.department:
            departments[member.department].append(member)

    cycle_ids = _reporting_cycle_member_ids(members)
    team = (snapshot or {}).get("team")
    explicit_manager_id = (
        uuid_or_none(team.get("manager_agent_profile_id")) if isinstance(team, dict) else None
    )
    managers = tuple(
        member
        for member in members
        if explicit_manager_id is not None and member.agent_profile_id == explicit_manager_id
    )
    leads = tuple(member for member in members if member.id in children and member not in managers)
    return OrgStructure(
        members=members,
        executives=(),
        managers=managers,
        leads=leads,
        contributors=tuple(
            member for member in members if member not in managers and member not in leads
        ),
        departments={
            department: tuple(sorted(items, key=_member_sort_key))
            for department, items in sorted(departments.items())
        },
        children_by_member_id={
            member_id: tuple(sorted(items, key=_member_sort_key))
            for member_id, items in children.items()
        },
        cycle_member_ids=tuple(sorted(cycle_ids)),
        orphan_member_ids=tuple(sorted(orphan_ids)),
    )


def normalize_role(role: str | None) -> str:
    return (role or "").strip().lower().replace(" ", "_").replace("-", "_")


def _org_members(snapshot: dict[str, object] | None) -> list[OrgMember]:
    if not isinstance(snapshot, dict):
        return []
    raw_members = snapshot.get("members")
    if not isinstance(raw_members, list):
        return []
    members: list[OrgMember] = []
    for raw_member in raw_members:
        if not isinstance(raw_member, dict):
            continue
        member_id = non_empty_string_or_none(raw_member.get("id"))
        if member_id is None:
            continue
        team_role = str(raw_member.get("team_role") or "")
        members.append(
            OrgMember(
                id=member_id,
                agent_profile_id=uuid_or_none(raw_member.get("agent_profile_id")),
                reports_to_member_id=non_empty_string_or_none(
                    raw_member.get("reports_to_member_id")
                ),
                team_role=team_role,
                normalized_role=normalize_role(team_role),
                department=non_empty_string_or_none(raw_member.get("department")),
                position_title=non_empty_string_or_none(raw_member.get("position_title")),
                responsibilities=tuple(string_list(raw_member.get("responsibilities"))),
                order_index=_int_or_default(raw_member.get("order_index"), 0),
                raw=raw_member,
            )
        )
    return sorted(members, key=_member_sort_key)


def _reporting_cycle_member_ids(members: tuple[OrgMember, ...]) -> set[str]:
    reports_to = {
        member.id: member.reports_to_member_id
        for member in members
        if member.reports_to_member_id is not None
    }
    cycle_ids: set[str] = set()
    for member in members:
        seen: set[str] = set()
        current: str | None = member.id
        while current is not None:
            if current in seen:
                cycle_ids.update(seen)
                break
            seen.add(current)
            current = reports_to.get(current)
    return cycle_ids


def _member_sort_key(member: OrgMember) -> tuple[int, str, str]:
    return (member.order_index, member.normalized_role, member.id)


def _int_or_default(value: object, default: int) -> int:
    return value if isinstance(value, int) else default
