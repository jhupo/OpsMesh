from __future__ import annotations

from backend.app.orchestration.definitions.templates.normalization import int_or_default


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
