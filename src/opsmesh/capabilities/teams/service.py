"""Team version coherence: members resolve to published expert versions.

The team service checks that the fixed dependencies of a team version
pin exactly the experts its members name — no member left unresolved and
no dependency filling no member. It stores no collaboration state: the
team definition is the graph, and collaboration runs elsewhere.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy.orm import Session

from opsmesh.capabilities.models import CAPABILITY_TYPE_EXPERT, CapabilityVersion
from opsmesh.capabilities.teams.schemas import validated_configuration
from opsmesh.capabilities.validation import (
    PackageIntake,
    verify_declared_resources,
    verify_flow_template_files,
)
from opsmesh.shared.errors import ConflictError


def validate_configuration(configuration: Mapping[str, object]) -> dict[str, object]:
    """Validate one team configuration through the shared pydantic contract."""
    return validated_configuration(configuration)


def intake_package(
    files: Mapping[str, bytes],
    *,
    configuration: Mapping[str, object],
    resource_manifest: Sequence[Mapping[str, object]],
    flow_templates: Sequence[Mapping[str, object]],
) -> PackageIntake:
    """Verify the resources a team package declares.

    A team is a graph over expert versions, not executable content; a
    package only carries declared resources, which must exist with their
    digests.
    """
    verify_declared_resources(files, resource_manifest)
    verify_flow_template_files(files, flow_templates)
    return PackageIntake(
        configuration=dict(configuration),
        resource_manifest=[dict(entry) for entry in resource_manifest],
        flow_templates=[dict(entry) for entry in flow_templates],
    )


def validate_version(session: Session, version: CapabilityVersion) -> None:
    """Every member resolves through exactly one expert fixed dependency."""
    members: list[tuple[str, str]] = []
    raw_members = version.configuration.get("members")
    if isinstance(raw_members, list):
        for entry in raw_members:
            if isinstance(entry, dict):
                slot = entry.get("slot")
                expert_key = entry.get("expert_key")
                if isinstance(slot, str) and isinstance(expert_key, str):
                    members.append((slot, expert_key))
    if not members:
        raise ConflictError("A team version must declare at least one member")
    expert_keys = {expert_key for _, expert_key in members}
    pinned: set[str] = set()
    for dependency in version.dependencies:
        if dependency.get("type") != CAPABILITY_TYPE_EXPERT:
            raise ConflictError(
                "A team may only pin expert versions",
                details={"type": dependency.get("type")},
            )
        key = dependency.get("key")
        if not isinstance(key, str):
            raise ConflictError("A fixed dependency is malformed")
        if key not in expert_keys:
            raise ConflictError(
                "A fixed dependency fills no declared team member",
                details={"expert_key": key},
            )
        pinned.add(key)
    unresolved = sorted(expert_keys - pinned)
    if unresolved:
        raise ConflictError(
            "A team member has no pinned expert version",
            details={"expert_keys": unresolved},
        )
