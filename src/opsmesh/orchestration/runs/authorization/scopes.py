"""Select an immutable agent scope from a validated execution graph."""

from uuid import UUID

from opsmesh.shared.utils import dict_or_empty, uuid_or_none


def agent_execution_scope(
    snapshot: dict[str, object],
    profile_id: UUID | None,
    catalog_fingerprint: str | None,
) -> dict[str, object]:
    if profile_id is None and catalog_fingerprint is None:
        return snapshot
    root_catalog = dict_or_empty(snapshot.get("capability_catalog"))
    if profile_id == uuid_or_none(snapshot.get("agent_profile_id")):
        if catalog_fingerprint != root_catalog.get("fingerprint"):
            raise ValueError("Execution agent capability scope changed")
        return snapshot
    pending = [*_entries(snapshot.get("agent_tools", [])), *_entries(snapshot.get("handoffs", []))]
    matches: list[dict[str, object]] = []
    while pending:
        target = pending.pop()
        pending.extend(_entries(target.get("agent_tools", [])))
        pending.extend(_entries(target.get("handoffs", [])))
        catalog = dict_or_empty(target.get("capability_catalog"))
        ref = dict_or_empty(target.get("target"))
        if profile_id != uuid_or_none(ref.get("profile_id")) or catalog_fingerprint != catalog.get(
            "fingerprint"
        ):
            continue
        scope = {
            **snapshot,
            **{
                key: target.get(key, {})
                for key in ("runtime_policy", "approval_policy", "memory_policy")
            },
            "agent_profile_id": str(profile_id),
            "capability_catalog": catalog,
            "allowed_tools": [
                dict_or_empty(item.get("descriptor")).get("name")
                for item in _entries(catalog.get("tools", []))
            ],
            "installed_skills": target.get("installed_skills", []),
        }
        matches.append(scope)
    if not matches or any(item != matches[0] for item in matches[1:]):
        raise ValueError("Execution agent is outside its frozen capability scope")
    return matches[0]


def _entries(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError("Execution agent graph is invalid")
    return value
